"""Safe first-time node pairing, prior to touching services or credentials."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError

from . import node_agent


def controller_is_local(controller, state):
    """Never enroll the server running the requested controller hostname."""
    url = urlsplit(controller)
    if (url.scheme != "https" or not url.hostname
            or url.username or url.password or url.query or url.fragment
            or url.path not in ("", "/") or url.port not in (None, 443)):
        raise ValueError("Controller must be a plain HTTPS origin on port 443")
    return url.hostname.rstrip(".").lower() == state["panel_hostname"].rstrip(".").lower()


def preflight(controller, node_id, token, current_state, transport=None):
    if controller_is_local(controller, current_state):
        raise ValueError("Refusing to enroll central panel as a remote node")
    settings = {
        "controller": controller.rstrip("/"),
        "id": node_id,
        "token": token,
        "original_hostname": current_state["hostname"],
    }
    fetch = transport or node_agent.request_controller
    try:
        doc = fetch(settings, "GET", "/api/nodes/" + node_id + "/desired")
    except HTTPError as exc:
        raise ValueError(
            "Controller rejected node enrollment: HTTP " + str(exc.code)
        ) from None
    except (URLError, OSError, TimeoutError):
        raise ValueError("Controller HTTPS unreachable during enrollment") from None
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Controller did not return valid JSON") from None
    node_agent.verify_desired(doc, settings, current_state)
    return settings


def enrollment_hint(error):
    """Operator-safe errors only; never print credential, headers or response."""
    message = str(error)
    if "Controller rejected node enrollment: HTTP 502" in message:
        return (
            "The CENTRAL panel returned HTTP 502. On the central VPS, run: "
            "sudo bash /opt/blue-mtproxy/vps/recover-controller.sh ; "
            "sudo bash /opt/blue-mtproxy/vps/diagnose-login.sh"
        )
    if "Controller rejected node enrollment: HTTP 401" in message:
        return "Node pairing token or node ID is invalid. Check /nodes on the central panel."
    if "Controller rejected node enrollment: HTTP 403" in message:
        return "Central proxy or access policy blocked the node API. Check /api/nodes routes."
    if "primary hostname" in message:
        return "Proxy hostname on this VPS must equal the node primary domain in /nodes."
    return "Check central panel health and node enrollment settings."


def check_from_environment():
    import os
    import sys
    try:
        state = json.loads(Path("/var/lib/blue-mtproxy/state.json").read_text())
        preflight(os.environ["NODE_CONTROLLER"], os.environ["NODE_ID"],
                  os.environ["NODE_TOKEN"], state)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("[error] " + str(exc), file=sys.stderr)
        print("[hint] " + enrollment_hint(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print("[ok] Controller reachable, token valid, hostname matches remote VPS.")


if __name__ == "__main__":
    check_from_environment()
