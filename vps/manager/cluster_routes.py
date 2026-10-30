"""Authenticated cluster administration and bearer-protected node API."""
import copy
import secrets
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for

from . import cluster
from .model import CHANNEL, CARRIERS, HEX, new_secret, web_url, direct_url

bp = Blueprint("cluster", __name__)


def page(node_id=None, enrollment=None):
    doc = cluster.read()
    try:
        node = cluster.get_node(doc, node_id) if node_id else None
    except ValueError:
        abort(404)
    return render_template(
        "nodes.html", nodes=doc["nodes"], node=node,
        enrollment=enrollment, csrf=session["csrf"],
        statuses={n["id"]: cluster.node_status(n) for n in doc["nodes"]},
        web_url=web_url, direct_url=direct_url, carriers=sorted(CARRIERS),
    )


def change(callback, node_id=None):
    try:
        cluster.mutate(callback)
        flash("تنظیمات ذخیره شد؛ نود از طریق HTTPS آن‌ها را دریافت می‌کند.", "success")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        flash(str(exc)[:180], "error")
    return redirect(url_for("cluster.node_detail", node_id=node_id) if node_id
                    else url_for("cluster.nodes"))


@bp.get("/nodes")
def nodes():
    return page()


@bp.post("/nodes")
def create_node():
    try:
        node_id, token = cluster.mutate(
            lambda doc: cluster.add_node(doc, request.form.get("name", ""),
                                         request.form.get("location", ""),
                                         request.form.get("hostname", "").strip().lower()))
        # Token is deliberately never persisted or placed in a URL or cookie.
        return page(node_id, enrollment={"token": token, "id": node_id})
    except (ValueError, OSError) as exc:
        flash(str(exc)[:180], "error")
        return redirect(url_for("cluster.nodes"))


@bp.get("/nodes/<node_id>")
def node_detail(node_id):
    return page(node_id)


@bp.post("/nodes/<node_id>/domains")
def add_domain(node_id):
    hostname = request.form.get("hostname", "").strip().lower()

    def op(doc):
        node = cluster.get_node(doc, node_id)
        cluster.check_hostname(hostname)
        cluster.check_domain_available(doc, hostname, node_id)
        if hostname in cluster.node_domains(node):
            raise ValueError("دامنه قبلاً ثبت شده است")
        if len(cluster.node_domains(node)) >= 16:
            raise ValueError("حداکثر ۱۶ دامنه برای هر نود مجاز است")
        node["domains"].append(hostname)
        cluster.bump(node)
    return change(op, node_id)


@bp.post("/nodes/<node_id>/domains/remove")
def remove_domain(node_id):
    hostname = request.form.get("hostname", "")

    def op(doc):
        node = cluster.get_node(doc, node_id)
        if hostname not in node["domains"]:
            raise ValueError("دامنه قابل حذف نیست")
        if any(p.get("hostname", node["hostname"]) == hostname for p in node["profiles"]):
            raise ValueError("ابتدا دامنه اینباندهای وابسته را تغییر دهید")
        node["domains"].remove(hostname)
        cluster.bump(node)
    return change(op, node_id)


@bp.post("/nodes/<node_id>/inbounds")
def add_inbound(node_id):
    def op(doc):
        node = cluster.get_node(doc, node_id)
        cluster.new_profile(node, request.form.get("label", ""),
                            request.form.get("hostname", node["hostname"]))
    return change(op, node_id)


@bp.post("/nodes/<node_id>/inbounds/<profile_id>")
def edit_inbound(node_id, profile_id):
    def op(doc):
        node = cluster.get_node(doc, node_id)
        p = cluster.get_profile(node, profile_id)
        p["label"] = cluster.check_label(request.form.get("label", ""))
        p["hostname"] = request.form.get("hostname", "")
        p["carrier"] = request.form.get("carrier", "")
        p["channel"] = request.form.get("channel", "")
        p["ad_tag"] = request.form.get("ad_tag", "").strip().lower()
        p["enabled"] = request.form.get("enabled") == "on"
        cluster.bump(node)
    return change(op, node_id)


@bp.post("/nodes/<node_id>/inbounds/<profile_id>/rotate")
def rotate_inbound(node_id, profile_id):
    def op(doc):
        node = cluster.get_node(doc, node_id)
        cluster.get_profile(node, profile_id)["secret"] = new_secret()
        cluster.bump(node)
    return change(op, node_id)


@bp.post("/nodes/<node_id>/inbounds/<profile_id>/delete")
def delete_inbound(node_id, profile_id):
    def op(doc):
        node = cluster.get_node(doc, node_id)
        cluster.get_profile(node, profile_id)
        if len(node["profiles"]) == 1:
            raise ValueError("آخرین اینباند نود را حذف نکنید؛ ابتدا نود را غیرفعال یا بازنشانی کنید")
        node["profiles"] = [p for p in node["profiles"] if p["id"] != profile_id]
        cluster.bump(node)
    return change(op, node_id)


@bp.post("/nodes/<node_id>/inbounds/<profile_id>/move")
def move_inbound(node_id, profile_id):
    target_id = request.form.get("target_id", "")

    def op(doc):
        src = cluster.get_node(doc, node_id)
        dst = cluster.get_node(doc, target_id)
        if src["id"] == dst["id"]:
            raise ValueError("مقصد باید نود دیگری باشد")
        profile = cluster.get_profile(src, profile_id)
        if len(src["profiles"]) < 2:
            raise ValueError("انتقال آخرین اینباند نود مجاز نیست")
        slots = {p["slot"] for p in dst["profiles"]}
        slot = next((v for v in range(32) if v not in slots), None)
        if slot is None:
            raise ValueError("نود مقصد ظرفیت آزاد ندارد")
        copied = copy.deepcopy(profile)
        copied["slot"] = slot
        copied["hostname"] = dst["hostname"]
        src["profiles"] = [p for p in src["profiles"] if p["id"] != profile_id]
        dst["profiles"].append(copied)
        cluster.bump(src)
        cluster.bump(dst)
    result = change(op, node_id)
    return result


@bp.post("/nodes/<node_id>/inbounds/<profile_id>/users")
def add_user(node_id, profile_id):
    def op(doc):
        node = cluster.get_node(doc, node_id)
        p = cluster.get_profile(node, profile_id)
        p.setdefault("clients", []).append({
            "id": "u" + secrets.token_hex(6),
            "label": cluster.check_label(request.form.get("label", "")),
            "secret": new_secret(), "enabled": True,
        })
        cluster.bump(node)
    return change(op, node_id)


@bp.post("/nodes/<node_id>/inbounds/<profile_id>/users/<client_id>/<action>")
def edit_user(node_id, profile_id, client_id, action):
    if action not in ("toggle", "rotate", "delete"):
        abort(404)

    def op(doc):
        node = cluster.get_node(doc, node_id)
        p = cluster.get_profile(node, profile_id)
        clients = p.get("clients", [])
        matching = [u for u in clients if u["id"] == client_id]
        if not matching:
            raise ValueError("کاربر پیدا نشد")
        user = matching[0]
        if action == "toggle":
            user["enabled"] = not user["enabled"]
        elif action == "rotate":
            user["secret"] = new_secret()
        else:
            p["clients"] = [u for u in clients if u["id"] != client_id]
        cluster.bump(node)
    return change(op, node_id)


def verified_node(node_id):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or not cluster.authorize(node_id, header[7:]):
        abort(401)
    return cluster.get_node(cluster.read(), node_id)


@bp.get("/api/nodes/<node_id>/desired")
def node_desired(node_id):
    return jsonify(cluster.desired(verified_node(node_id)))


@bp.post("/api/nodes/<node_id>/report")
def node_report(node_id):
    verified_node(node_id)
    if not request.is_json:
        abort(415)
    data = request.get_json(silent=True)
    if type(data) is not dict:
        abort(400)
    try:
        cluster.mutate(lambda doc: cluster.record_report(
            doc, node_id, data.get("revision"), data.get("status"), data.get("error", "")))
    except (ValueError, TypeError):
        abort(400)
    return jsonify({"ok": True})
