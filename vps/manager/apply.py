"""Root-only, narrow declarative reconciler. State is untrusted until validated."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model import relay_profiles, validate

ROOT = Path("/var/lib/blue-mtproxy")
ETC = Path("/etc/blue-mtproxy/runtime")
PROFILES = Path("/etc/tproxy-server/profiles.json")
UNIT = "blue-mtproxy@{}.service"


def run(*args):
    subprocess.run(args, check=True, timeout=45)


def atomic(path, body, mode, group=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        if group is not None:
            os.chown(temp, 0, group)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def dump(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def reconcile():
    if os.geteuid() != 0:
        raise PermissionError("apply must run as root")
    with open("/run/lock/blue-mtproxy-apply.lock", "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # State file path is fixed, and O_NOFOLLOW prevents a panel-owned symlink redirect.
        fd = os.open(str(ROOT / "state.json"), os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with os.fdopen(fd, encoding="utf-8") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized state")
            state = validate(json.loads(raw))
        except Exception:
            raise
        active = [p for p in state["profiles"] if p["enabled"]]
        previous = PROFILES.read_bytes()
        candidate = dump(relay_profiles(state))
        runtime_group = __import__("grp").getgrnam("mtproxy").gr_gid
        relay_group = __import__("grp").getgrnam("tproxy").gr_gid
        ETC.mkdir(parents=True, exist_ok=True)
        os.chmod(ETC, 0o750)
        os.chown(ETC, 0, runtime_group)
        changed = set()
        for p in active:
            runtime_path = ETC / (p["id"] + ".json")
            runtime = dump({key: p[key] for key in ("id", "slot", "secret", "ad_tag")})
            if not runtime_path.exists() or runtime_path.read_bytes() != runtime:
                atomic(runtime_path, runtime, 0o640, runtime_group)
                changed.add(p["id"])
        relay_changed = previous != candidate
        if relay_changed:
            atomic(PROFILES, candidate, 0o640, relay_group)
        try:
            # The relay service runs as tproxy, not root. Root-only checks
            # cannot detect an unreadable profiles.json on service startup.
            run("runuser", "-u", "tproxy", "--", "test", "-r", str(PROFILES))
            run("/usr/local/bin/tproxy-server",
                "-config", "/etc/tproxy-server/config.json",
                "-profiles-file", str(PROFILES), "-check")
        except Exception:
            if relay_changed:
                atomic(PROFILES, previous, 0o640, relay_group)
            raise

        existing = {p.stem for p in ETC.glob("*.json")}
        desired = {p["id"] for p in active}
        # Use one managed process per secret so each profile has its own Ad Tag.
        run("systemctl", "disable", "--now", "mtproxy.service")
        for profile_id in sorted(existing - desired):
            run("systemctl", "disable", "--now", UNIT.format(profile_id))
            (ETC / (profile_id + ".json")).unlink(missing_ok=True)
        for profile_id in sorted(desired):
            run("systemctl", "enable", UNIT.format(profile_id))
            active_now = subprocess.run(
                ["systemctl", "is-active", "--quiet", UNIT.format(profile_id)],
                check=False, timeout=8,
            ).returncode == 0
            if profile_id in changed or not active_now:
                run("systemctl", "restart", UNIT.format(profile_id))
        if relay_changed:
            run("systemctl", "restart", "tproxy-server.service")


if __name__ == "__main__":
    try:
        reconcile()
    except Exception as exc:
        print("Unable to reconcile proxy services: " + str(exc), file=sys.stderr)
        sys.exit(1)
