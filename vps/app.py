"""Persian RTL management panel for the VPS Telegram WEB relay."""
import copy
import fcntl
import json
import os
import secrets
import subprocess
import tempfile
import time
from datetime import timedelta
from time import time as current_time
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash

from manager.model import new_secret, validate, web_url, direct_url, CARRIERS
from manager.control_client import dispatch

STATE = Path("/var/lib/blue-mtproxy/state.json")
CDN_FLAG = Path("/etc/blue-mtproxy/cdn-only")
LOCK = STATE.with_name("ui.lock")
app = Flask(__name__)
app.secret_key = os.environ["PANEL_SECRET_KEY"]
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=32768,
)
FAILURES = {}


@app.after_request
def security(response):
    response.headers.update({
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        "Strict-Transport-Security": "max-age=31536000",
    })
    return response


@app.before_request
def enforce():
    if request.path == "/healthz":
        return
    if request.method == "POST":
        if request.mimetype != "application/x-www-form-urlencoded":
            abort(415)
        provided = request.form.get("_csrf", "")
        expected = session.get("csrf", "")
        if not expected or not secrets.compare_digest(provided, expected):
            abort(403)
    if request.endpoint not in {"login", "health"} and not session.get("authenticated"):
        return redirect(url_for("login"))


@app.get("/healthz")
def health():
    return {"ok": True}


DEBUG_CACHE = Path("/var/cache/blue-mtproxy-debug/report.json")


def diagnostic_failure(code, message):
    return {
        "schema": 1, "overall": "fail",
        "checks": [{"code": code, "status": "fail", "message": message}],
        "profiles": [],
        "note": "برای نصب سرویس مستقل عیب‌یابی، اسکریپت install-debug.sh را در VPS اجرا کنید.",
    }


def collect_diagnostics():
    """Read a root-published snapshot; do not attempt sudo from Flask.

    NoNewPrivileges=yes intentionally blocks escalation within the web
    process. The isolated, root-only timer executes the probes instead.
    """
    try:
        stat = DEBUG_CACHE.stat()
        if stat.st_size > 32768:
            return diagnostic_failure("cache_invalid", "حجم گزارش عیب‌یابی نامعتبر است")
        if current_time() - stat.st_mtime > 300:
            return diagnostic_failure("cache_stale", "گزارش قدیمی است؛ تایمر عیب‌یابی اجرا نمی‌شود")
        report = json.loads(DEBUG_CACHE.read_bytes().decode("utf-8"))
        if (report.get("schema") != 1 or
                not isinstance(report.get("checks"), list) or
                not isinstance(report.get("profiles"), list)):
            return diagnostic_failure("cache_invalid", "گزارش معتبر نیست")
        # Show the age of the root-generated snapshot so a recently repaired
        # backend is not mistaken for a fresh failure.
        report["report_age_seconds"] = max(0, int(current_time() - stat.st_mtime))
        return report
    except FileNotFoundError:
        return diagnostic_failure("cache_missing", "سرویس مستقل عیب‌یابی هنوز گزارش تولید نکرده است")
    except PermissionError:
        return diagnostic_failure("cache_permission", "کاربر پنل اجازه خواندن گزارش را ندارد")
    except (OSError, ValueError, TypeError, UnicodeError):
        return diagnostic_failure("cache_invalid", "خطا در خواندن فایل گزارش؛ ژورنال عیب‌یاب را بررسی کنید")


@app.get("/debug")
def debug_dashboard():
    report = collect_diagnostics()
    return render_template_string(
        DEBUG_HTML, report=report,
        checks=report.get("checks", []), profiles=report.get("profiles", []),
        cdn_only=CDN_FLAG.is_file(),
    )


@app.get("/debug.json")
def debug_json():
    return collect_diagnostics()


def fresh_token():
    session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def load_state():
    return validate(json.loads(STATE.read_text(encoding="utf-8")))


def write_state(state):
    validate(state)
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=STATE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def apply_mutation(change):
    LOCK.touch(mode=0o600, exist_ok=True)
    with LOCK.open("r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = load_state()
        next_state = copy.deepcopy(old)
        change(next_state)
        validate(next_state)
        write_state(next_state)
        try:
            # This sends a fixed action over a bluepanel-only UNIX socket.
            # The web service retains NoNewPrivileges=yes and never uses sudo.
            dispatch("apply")
        except Exception:
            write_state(old)
            # Best-effort restore of the previously deployed state. The
            # root-owned broker validates state and owns every system change.
            try:
                dispatch("apply")
            except (OSError, RuntimeError):
                pass
            raise


def get_profile(state, profile_id):
    for p in state["profiles"]:
        if p["id"] == profile_id:
            return p
    raise ValueError("پروکسی پیدا نشد")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        attempts = FAILURES.get("admin", [])
        now = time.monotonic()
        attempts = [t for t in attempts if now - t < 600]
        if len(attempts) >= 8:
            flash("ورود برای چند دقیقه محدود شده است.", "error")
        elif secrets.compare_digest(username, "admin") and check_password_hash(
            os.environ["PANEL_ADMIN_PASSWORD_HASH"], password
        ):
            session.clear()
            session.permanent = True
            session["authenticated"] = True
            fresh_token()
            FAILURES.clear()
            return redirect(url_for("index"))
        else:
            attempts.append(now)
            FAILURES["admin"] = attempts
            flash("نام کاربری یا رمز عبور اشتباه است.", "error")
    if not session.get("csrf"):
        fresh_token()
    return render_template_string(LOGIN_HTML, csrf=session["csrf"])


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def render_manager(section="overview", selected_id=None):
    state = load_state()
    report = collect_diagnostics()
    telemetry = {p["id"]: p for p in report.get("profiles", []) if isinstance(p, dict) and "id" in p}
    services = {}
    for p in state["profiles"]:
        item = telemetry.get(p["id"], {})
        status = item.get("mtproxy_service")
        # The cached diagnostics provide definitive results; do not pretend
        # failed/unavailable monitoring means the backend is offline.
        services[p["id"]] = status if status in ("ok", "fail") else "unknown"
    selected = get_profile(state, selected_id) if selected_id else None
    user_records = [(p, c) for p in state["profiles"] for c in p.get("clients", [])]
    total_connections = [p["active_connections"] for p in telemetry.values()
                         if type(p.get("active_connections")) is int]
    return render_template(
        "manager.html", state=state, services=services, telemetry=telemetry,
        section=section, selected=selected, users=user_records,
        total_users=len(user_records), total_connections=(sum(total_connections)
        if total_connections and len(total_connections) ==
           sum(bool(p["enabled"]) for p in state["profiles"]) else None),
        cdn_only=CDN_FLAG.is_file(), csrf=session["csrf"],
        carriers=sorted(CARRIERS), report=report,
        links={p["id"]: web_url(state["hostname"], p["secret"])
               for p in state["profiles"]},
        direct_links={p["id"]: direct_url(state["hostname"], p["secret"], 10443 + p["slot"])
                      for p in state["profiles"]},
        client_web_url=web_url, client_direct_url=direct_url,
    )


@app.get("/")
def index():
    return render_manager()


@app.get("/inbounds")
def inbounds():
    return render_manager("inbounds")


@app.get("/inbounds/<profile_id>")
def inbound_detail(profile_id):
    try:
        return render_manager("detail", profile_id)
    except ValueError:
        abort(404)


@app.get("/users")
def users():
    return render_manager("users")


@app.get("/settings")
def settings_page():
    return render_manager("settings")


@app.post("/profiles")
def add_profile():
    label = request.form.get("label", "").strip()
    def change(state):
        occupied = {p["slot"] for p in state["profiles"]}
        slot = next((i for i in range(32) if i not in occupied), None)
        if slot is None:
            raise ValueError("حداکثر ۳۲ پروکسی قابل تعریف است")
        state["profiles"].append({
            "id": "p" + secrets.token_hex(4),
            "slot": slot,
            "label": label or "New Proxy",
            "secret": new_secret(),
            "ad_tag": "",
            "channel": "",
            "enabled": True,
            "carrier": "websocket",
        })
    return mutate(change, "پروکسی ساخته شد")


@app.post("/profiles/<profile_id>")
def update_profile(profile_id):
    def change(state):
        p = get_profile(state, profile_id)
        p["label"] = request.form.get("label", "").strip()
        p["secret"] = request.form.get("secret", "").strip().lower()
        p["channel"] = request.form.get("channel", "").strip()
        p["ad_tag"] = request.form.get("ad_tag", "").strip().lower()
        p["carrier"] = request.form.get("carrier", "").strip()
        p["enabled"] = request.form.get("enabled") == "on"
    return mutate(change, "تنظیمات ثبت شد")


@app.post("/profiles/<profile_id>/rotate")
def rotate_profile(profile_id):
    def change(state):
        get_profile(state, profile_id)["secret"] = new_secret()
    return mutate(change, "Secret جدید ساخته شد؛ لینک‌های قبلی دیگر معتبر نیستند")


@app.post("/profiles/<profile_id>/delete")
def delete_profile(profile_id):
    def change(state):
        state["profiles"] = [p for p in state["profiles"] if p["id"] != profile_id]
    return mutate(change, "پروکسی حذف شد")


def client_in_inbound(profile, client_id):
    for c in profile.get("clients", []):
        if c["id"] == client_id:
            return c
    raise ValueError("کاربر در این اینباند وجود ندارد")


def mutate_client(change, inbound_id, message):
    try:
        apply_mutation(change)
        flash(message, "success")
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        flash(str(exc), "error")
    return redirect(url_for("inbound_detail", profile_id=inbound_id))


@app.post("/inbounds/<profile_id>/users")
def create_user(profile_id):
    label = request.form.get("label", "").strip()
    def change(state):
        p = get_profile(state, profile_id)
        p.setdefault("clients", []).append({
            "id": "u" + secrets.token_hex(4),
            "label": label or "User",
            "secret": new_secret(),
            "enabled": True,
        })
    return mutate_client(change, profile_id, "کاربر با Secret اختصاصی ساخته شد")


@app.post("/inbounds/<profile_id>/users/<client_id>")
def edit_user(profile_id, client_id):
    def change(state):
        client = client_in_inbound(get_profile(state, profile_id), client_id)
        client["label"] = request.form.get("label", "").strip()
        client["enabled"] = request.form.get("enabled") == "on"
    return mutate_client(change, profile_id, "دسترسی کاربر به‌روزرسانی شد")


@app.post("/inbounds/<profile_id>/users/<client_id>/rotate")
def rotate_user(profile_id, client_id):
    def change(state):
        client_in_inbound(get_profile(state, profile_id), client_id)["secret"] = new_secret()
    return mutate_client(change, profile_id, "Secret جدید صادر شد؛ لینک قدیمی کاربر باطل شد")


@app.post("/inbounds/<profile_id>/users/<client_id>/delete")
def delete_user(profile_id, client_id):
    def change(state):
        p = get_profile(state, profile_id)
        client_in_inbound(p, client_id)
        p["clients"] = [c for c in p.get("clients", []) if c["id"] != client_id]
    return mutate_client(change, profile_id, "کاربر حذف و Secret او از سرویس خارج شد")


@app.post("/settings/auto-update")
def auto_update():
    def change(state):
        state["auto_update"] = request.form.get("enabled") == "on"
    return mutate(change, "تنظیمات آپدیت ذخیره شد")


@app.post("/settings/check-update")
def check_update():
    try:
        dispatch("update")
        flash("بررسی به‌روزرسانی در سرویس مستقل آغاز شد؛ نتیجه در ژورنال systemd ثبت می‌شود.", "success")
    except (ValueError, RuntimeError, OSError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


def mutate(change, ok):
    try:
        apply_mutation(change)
        flash(ok, "success")
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


STYLE = """
:root{color-scheme:dark;font-family:Vazirmatn,Tahoma,sans-serif;background:#0b1020;color:#e9eefb}
*{box-sizing:border-box}body{margin:0;direction:rtl;line-height:1.6}
.shell{max-width:1160px;margin:auto;padding:26px 18px 75px}
nav{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.logo{font-weight:900;font-size:22px;letter-spacing:-.5px}
.mark{display:inline-grid;place-items:center;width:38px;height:38px;border-radius:13px;background:linear-gradient(135deg,#16b7cd,#3b7aff);margin-left:9px;color:white}
.sub{color:#94a4c5;font-size:13px}
h1{font-size:clamp(25px,4vw,34px);margin:32px 0 2px}h2{font-size:20px}
.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin:22px 0}
.card{background:#141d33;border:1px solid #26324a;border-radius:18px;padding:20px;min-width:0}
.metric{font-size:27px;font-weight:800;color:#dff7ff}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.profile{display:flex;flex-direction:column;gap:11px}
input,select{font:inherit;width:100%;background:#0d1526;color:#edf4ff;border:1px solid #34435f;border-radius:10px;padding:10px 12px;outline:none;min-width:0}
input:focus,select:focus{border-color:#2cb8d2;box-shadow:0 0 0 3px #2cb8d222}
label{font-size:12px;color:#b6c7e4;display:block;margin-bottom:5px}
button{font:inherit;font-weight:700;color:white;border:0;border-radius:10px;padding:10px 16px;cursor:pointer;background:#1364dc}
button.secondary{background:#25334d}button.danger{background:#823544}
button:hover{filter:brightness(1.16)}
actions,.actions{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
form{margin:0} .inline{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.header{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.pill{display:inline-flex;font-size:12px;padding:3px 10px;border-radius:99px;background:#364051;color:#cbd8ef}
.pill.ok{background:#123d39;color:#57dfbf}
code{font-family:ui-monospace,monospace;font-size:12px;overflow-wrap:anywhere;color:#89d9ee}
.notice{border:1px solid #274760;background:#0e2a3b;padding:12px 15px;border-radius:12px;margin:15px 0;font-size:13px}
.notice.error{border-color:#82454f;background:#3c1d2c}
a{color:#66d7f5}hr{border:0;border-top:1px solid #28364d;margin:18px 0}
small{color:#8e9db8}.switch{display:flex;align-items:center;gap:8px}.switch input{width:18px;height:18px}
@media(max-width:820px){.grid{grid-template-columns:1fr}.cards{grid-template-columns:1fr 1fr}}
@media(max-width:480px){.cards{grid-template-columns:1fr}.shell{padding:18px 12px 50px}.card{padding:16px}}
"""

LOGIN_HTML = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Blue Proxy · Login</title>
<style>""" + STYLE + """</style></head><body><main class="shell" style="max-width:420px;padding-top:12vh">
<div class="logo"><span class="mark">B</span>Blue Proxy</div>
<h1>ورود به مدیریت</h1><p class="sub">ورود امن به پنل مدیریت پروکسی</p>
{% for category,message in get_flashed_messages(with_categories=true) %}<div class="notice {{ 'error' if category=='error' else '' }}">{{message}}</div>{% endfor %}
<form method="post" class="card" style="display:grid;gap:15px">
<input type="hidden" name="_csrf" value="{{csrf}}">
<div><label>نام کاربری</label><input required name="username" autocomplete="username" value="admin"></div>
<div><label>رمز عبور</label><input required type="password" name="password" autocomplete="current-password"></div>
<button type="submit">ورود به داشبورد</button></form></main></body></html>"""

DASHBOARD_HTML = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Blue Proxy Manager</title>
<style>""" + STYLE + """</style></head><body><main class="shell">
<nav><div><div class="logo"><span class="mark">B</span>Blue Proxy Manager</div><div class="sub">WEB Proxy · VPS Control Center</div></div>
<div class="actions"><a href="{{url_for('debug_dashboard')}}" class="pill">🩺 /debug</a></div>
<form action="{{url_for('logout')}}" method="post"><input type="hidden" name="_csrf" value="{{csrf}}"><button class="secondary">خروج امن</button></form></nav>
<h1>داشبورد مدیریت</h1><p class="sub">مدیریت مستقل Secret، کانال اسپانسر و Ad Tag در یک سرور</p>
{% for category,message in get_flashed_messages(with_categories=true) %}<div class="notice {{ 'error' if category=='error' else '' }}">{{message}}</div>{% endfor %}
<section class="cards">
<div class="card"><div class="sub">کل پروکسی‌ها</div><div class="metric">{{state.profiles|length}}</div></div>
<div class="card"><div class="sub">پروکسی‌های فعال</div><div class="metric">{{state.profiles|selectattr('enabled')|list|length}}</div></div>
<div class="card"><div class="sub">دارای Ad Tag</div><div class="metric">{{state.profiles|selectattr('ad_tag')|list|length}}</div></div>
</section>
<div class="header"><h2>لیست پروکسی‌ها</h2><div class="pill">{{state.hostname}}</div></div>
<section class="grid">
{% for p in state.profiles %}
<article class="card profile">
<div class="header"><strong>{{p.label}}</strong><span class="pill {{'ok' if services[p.id]=='active' else ''}}">{{services[p.id] or 'unknown'}}</span></div>
<div class="sub">شناسه {{p.id}} · پورت داخلی MTProxy {{10443+p.slot}}</div>
{% if cdn_only %}
<div class="notice">حالت Cloudflare CDN-only فعال است. اتصال عمومی فقط از مسیر HTTPS/WSS پورت 443 انجام می‌شود؛ MTProxy روی این پورت داخلی است و لینک استاندارد MTProto از CDN عادی عبور نمی‌کند.</div>
<div><label>لینک WEB آزمایشی از مسیر Cloudflare (کلاینت سازگار ضروری است)</label><code>{{links[p.id]}}</code></div>
{% else %}
<div><label>لینک مستقیم MTProto (خارج از CDN)</label><code>{{direct_links[p.id]}}</code></div>
<div><label>لینک WEB آزمایشی (HTTPS/WSS 443)</label><code>{{links[p.id]}}</code></div>
{% endif %}
<div class="sub">WEB بدون پشتیبانی ویژه در کلاینت ممکن است «غیر قابل دسترس» نشان داده شود؛ سلامت SSL کافی نیست.</div>
<form action="{{url_for('update_profile',profile_id=p.id)}}" method="post" class="profile">
<input type="hidden" name="_csrf" value="{{csrf}}">
<div><label>عنوان پروکسی</label><input required maxlength="80" name="label" value="{{p.label}}"></div>
<div><label>Secret (32-hex؛ تغییر آن لینک‌های قبلی را غیرفعال می‌کند)</label><input required maxlength="32" minlength="32" pattern="[0-9a-fA-F]{32}" name="secret" value="{{p.secret}}" autocomplete="off"></div>
<div><label>کانال اسپانسر (برای مدیریت داخلی)</label><input name="channel" placeholder="@mychannel" value="{{p.channel}}"></div>
<div><label>Ad Tag دریافتی از @MTProxyBot</label><input name="ad_tag" maxlength="32" pattern="[0-9a-fA-F]{32}" placeholder="32 hex chars" value="{{p.ad_tag}}"></div>
<div><label>حالت انتقال WEB</label><select name="carrier">{% for m in carriers %}<option value="{{m}}" {{'selected' if p.carrier==m else ''}}>{{m}}</option>{% endfor %}</select></div>
<label class="switch"><input type="checkbox" name="enabled" {{'checked' if p.enabled else ''}}> فعال باشد</label>
<button>ذخیره تنظیمات</button></form>
<div class="actions">
<form action="{{url_for('rotate_profile',profile_id=p.id)}}" method="post"><input type="hidden" name="_csrf" value="{{csrf}}"><button class="secondary" title="Old links stop working">تولید Secret جدید</button></form>
<form action="{{url_for('delete_profile',profile_id=p.id)}}" method="post"><input type="hidden" name="_csrf" value="{{csrf}}"><button class="danger">حذف</button></form>
</div>
</article>
{% endfor %}
<article class="card profile">
<h2>افزودن پروکسی</h2><p class="sub">هر پروکسی Secret و نمونه MTProxy مستقل دارد و امکان تنظیم Ad Tag جداگانه فراهم است.</p>
<form action="{{url_for('add_profile')}}" method="post" class="profile"><input type="hidden" name="_csrf" value="{{csrf}}">
<div><label>نام پروکسی جدید</label><input required maxlength="80" name="label" placeholder="Sponsor A"></div><button>+ ساخت پروکسی</button></form>
</article></section>
<section class="card" style="margin-top:18px">
<h2>آپدیت خودکار از GitHub</h2>
<div class="sub">شاخه main · hazhanhasani/mtproto · بررسی دوره‌ای با systemd timer</div><hr>
<form action="{{url_for('auto_update')}}" method="post" class="inline">
<input type="hidden" name="_csrf" value="{{csrf}}"><label class="switch"><input type="checkbox" name="enabled" {{'checked' if state.auto_update else ''}}> آپدیت خودکار فعال</label>
<button class="secondary">ذخیره</button></form>
<hr><form action="{{url_for('check_update')}}" method="post"><input type="hidden" name="_csrf" value="{{csrf}}"><button>بررسی آپدیت اکنون</button></form>
</section><div class="notice">
کانال اسپانسر توسط Telegram و Ad Tag رسمی نمایش داده می‌شود؛ تغییر فیلد کانال به‌تنهایی باعث ثبت آن در تلگرام نخواهد شد.
</div><small>Proxy carrier: experimental WEB. Only WEB-capable Telegram clients can use these links.</small>
</main></body></html>"""


DEBUG_HTML = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Blue Proxy · Debug</title>
<style>""" + STYLE + """
.debug-status{font-size:12px;font-weight:700;border-radius:12px;padding:4px 9px;display:inline-block}
.debug-status.ok{background:#0f4538;color:#72edc3}
.debug-status.fail{background:#562331;color:#ffa9aa}
.debug-status.warn,.debug-status.skipped{background:#513d19;color:#f9d589}
.debug-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.debug-row{display:flex;justify-content:space-between;align-items:center;gap:12px;
           border-bottom:1px solid #25314b;padding:12px 0}
.debug-row:last-child{border-bottom:0}
@media(max-width:760px){.debug-grid{grid-template-columns:1fr}}
</style></head><body><main class="shell">
<nav><div class="logo"><span class="mark">B</span>Blue Proxy /debug</div>
<div class="actions"><a href="{{url_for('index')}}">بازگشت به پنل</a><a href="{{url_for('debug_json')}}" class="pill">گزارش JSON بدون Secret</a></div></nav>
<h1>عیب‌یابی اتصال پروکسی</h1>
<p class="sub">بررسی مستقیم سرویس‌ها، WEB Bridge و MTProxy؛ بدون تغییر تنظیمات یا افشای Secret</p>
<div class="notice">WEB یک پروتکل آزمایشی است. حتی اگر تمامی آزمون‌های سرور سالم باشند،
پشتیبانی کلاینت تلگرام و اتصال واقعی آن هنوز باید جداگانه آزمایش شود.
{% if cdn_only %}
حالت CDN-only فعال است؛ لینک مستقیم MTProto از Cloudflare HTTP CDN عبور نمی‌کند و پورت داخلی از اینترنت مسدود شده است.
{% else %}
برای استفاده عمومی، لینک MTProto استاندارد را نیز امتحان کنید.
{% endif %}</div>
<section class="card">
<div class="header"><h2>وضعیت سرور</h2><span class="debug-status {{report.overall}}">
{{ 'نیازمند بررسی' if report.overall=='fail' else 'بررسی سرور انجام شد؛ اتصال کلاینت تأیید نشده' }}</span></div>
{% for item in checks %}
<div class="debug-row"><div><strong>{{item.code}}</strong><div class="sub">{{item.message}}</div></div>
<span class="debug-status {{item.status}}">{{item.status}}</span></div>
{% endfor %}
</section>
<h2>پروفایل‌ها</h2>
<section class="debug-grid">
{% for p in profiles %}
<article class="card">
<div class="header"><strong>{{p.id}}</strong><span class="pill">{{p.carrier}}</span></div>
<p class="sub">پورت MTProto: {{p.port}}</p>
{% for key,title in [('mtproxy_service','سرویس MTProxy'),('mtproxy_tcp','اتصال TCP داخلی'),
                     ('relay_config','هماهنگی Relay و Secret'),('web_bridge','WEB Bridge احراز هویت‌شده')] %}
<div class="debug-row"><span>{{title}}</span>
<span class="debug-status {{p[key]}}">{{p[key]}}</span></div>
{% endfor %}
<div class="sub">Ad Tag: {{'ثبت شده در پنل (نمایش اسپانسر تأیید نشده)' if p.ad_tag_configured else 'تنظیم نشده'}}</div>
</article>
{% endfor %}
</section>
<div class="card" style="margin-top:18px">
<h2>اشتراک‌گذاری گزارش امن</h2>
<p class="sub">نسخه JSON برای ارسال نتیجهٔ عیب‌یابی آماده است. اطلاعات ورود، Secret، آدرس
احراز هویت WEB و فایل‌های تنظیمات در گزارش نمایش داده نمی‌شوند.</p>
<a href="{{url_for('debug_json')}}">بازکردن /debug.json</a>
<p class="sub">زمان بررسی: {{report.get('generated_at','—')}} · سن گزارش: {{report.get('report_age_seconds','نامشخص')}} ثانیه · {{report.note}}</p>
</div>
</main></body></html>"""
