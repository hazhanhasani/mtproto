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
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash

from manager.model import new_secret, validate, web_url, direct_url, CARRIERS

STATE = Path("/var/lib/blue-mtproxy/state.json")
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


def collect_diagnostics():
    """Read-only, fixed-argument privileged helper; never surface raw stderr."""
    try:
        proc = subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/blue-mtproxy-debug"],
            capture_output=True, text=True, timeout=38, check=False,
        )
        if proc.returncode != 0 or len(proc.stdout) > 30000:
            raise RuntimeError("debug helper returned an error")
        report = json.loads(proc.stdout)
        if report.get("schema") != 1 or not isinstance(report.get("checks"), list):
            raise ValueError("invalid diagnostic schema")
        return report
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return {
            "schema": 1, "overall": "fail",
            "checks": [{"code": "helper", "status": "fail",
                        "message": "عیب‌یاب اجرا نشد؛ sudoers و سرویس پنل را بررسی کنید"}],
            "profiles": [],
            "note": "برای بررسی خطا از دستور sudo bash /opt/blue-mtproxy/vps/install-debug.sh استفاده کنید.",
        }


@app.get("/debug")
def debug_dashboard():
    report = collect_diagnostics()
    return render_template_string(
        DEBUG_HTML, report=report,
        checks=report.get("checks", []), profiles=report.get("profiles", []),
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
            proc = subprocess.run(
                ["sudo", "-n", "/usr/local/sbin/blue-mtproxy-apply"],
                capture_output=True, text=True, timeout=100, check=False,
            )
            if proc.returncode:
                raise RuntimeError("خطا در اعمال تنظیمات سرویس‌ها. " + proc.stderr[-300:])
        except Exception:
            write_state(old)
            # Restore the previous deployed state when reconciliation failed.
            subprocess.run(
                ["sudo", "-n", "/usr/local/sbin/blue-mtproxy-apply"],
                capture_output=True, timeout=100, check=False,
            )
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


@app.get("/")
def index():
    state = load_state()
    services = {}
    for p in state["profiles"]:
        proc = subprocess.run(
            ["systemctl", "is-active", "blue-mtproxy@" + p["id"] + ".service"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        services[p["id"]] = proc.stdout.strip()
    return render_template_string(
        DASHBOARD_HTML,
        state=state,
        services=services,
        links={p["id"]: web_url(state["hostname"], p["secret"]) for p in state["profiles"]},
        direct_links={p["id"]: direct_url(state["hostname"], p["secret"], 10443+p["slot"]) for p in state["profiles"]},
        csrf=session["csrf"],
        carriers=sorted(CARRIERS),
    )


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


@app.post("/settings/auto-update")
def auto_update():
    def change(state):
        state["auto_update"] = request.form.get("enabled") == "on"
    return mutate(change, "تنظیمات آپدیت ذخیره شد")


@app.post("/settings/check-update")
def check_update():
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", "start", "--no-block", "blue-mtproxy-update-force.service"],
            capture_output=True, text=True, timeout=180, check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-400:])
        flash("بررسی به‌روزرسانی در سرویس مستقل آغاز شد؛ نتیجه در ژورنال systemd ثبت می‌شود.", "success")
    except Exception as exc:
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
<div class="sub">شناسه {{p.id}} · پورت MTProto {{10443+p.slot}}</div>
<div><label>لینک اصلی MTProto (مناسب کلاینت‌های استاندارد تلگرام)</label><code>{{direct_links[p.id]}}</code></div>
<div><label>لینک WEB آزمایشی (تنها برای کلاینت دارای WEB Adapter)</label><code>{{links[p.id]}}</code></div>
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
برای استفاده عمومی، لینک MTProto استاندارد را نیز امتحان کنید.</div>
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
<p class="sub">زمان بررسی: {{report.get('generated_at','—')}} · {{report.note}}</p>
</div>
</main></body></html>"""
