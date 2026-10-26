"""Orca: is it running, and are its settings ones bw can live with.

bw reads Orca and never writes it: presence through `orca status --json`, the version from
the app bundle's Info.plist, and the active profile's settings through a sealed copy of its
profile database (`lead.sqlite_ro`). Every missing or malformed piece is `unknown` with a
reason, never an exception.
"""
import json
import os
import plistlib
import re

from lead import proc, sqlite_ro
from lead.checks import Check

# Read from Orca 1.4.240's app bundle and a copy of its profile database, 2026-10-25.
# `agentPermissionDefault` takes one of own / ask / accept-edits / auto / full-access.
# "Manual" in Agents → Agent Permissions is the state with no permission flag added: Orca's
# onboarding writes "own" for it; "ask" ("Ask for approval") asks before edits and commands
# too. Orca's onboarding counts "full-access" and "auto" ("Approve for me") as its yolo
# choice, so both warn. "accept-edits" approves file edits unasked: unknown, named.
MANUAL_VALUES = {"own", "ask"}
FULL_ACCESS_VALUES = {"full-access", "auto"}
SETTINGS_TABLE = "profile_state_documents"
SETTINGS_KEY_COLUMN = "domain"
SETTINGS_PAYLOAD_COLUMN = "payload"
SETTINGS_DOMAIN = "settings"

FULL_ACCESS_WARNING = "Orca starts agents with full access; set Agents → Agent Permissions → Manual"
_PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def support_dir():
    return os.environ.get("BW_ORCA_SUPPORT") or os.path.expanduser("~/Library/Application Support/orca")


def running(run=proc.run):
    """(True | False | None, reason) from `orca status --json`."""
    r = run(["orca", "status", "--json"], 5)
    if r.timed_out:
        return None, "orca status timed out"
    if r.rc == 127:
        return None, "orca CLI not found"
    try:
        doc = json.loads(r.out)
    except ValueError:
        return None, f"orca status: not JSON (exit {r.rc})"
    result = doc.get("result") if isinstance(doc, dict) else None
    app = result.get("app") if isinstance(result, dict) else None
    flag = app.get("running") if isinstance(app, dict) else None
    if flag is True:
        return True, "running"
    if flag is False:
        return False, "not running"
    return None, "orca status: no result.app.running"


def app_version(app_path="/Applications/Orca.app"):
    try:
        with open(os.path.join(app_path, "Contents", "Info.plist"), "rb") as f:
            v = plistlib.load(f).get("CFBundleShortVersionString")
    except (OSError, ValueError, plistlib.InvalidFileException, AttributeError):
        return "unreadable"
    return v if isinstance(v, str) and v else "unreadable"


def _settings_row(conn):
    return conn.execute(f"SELECT {SETTINGS_PAYLOAD_COLUMN} FROM {SETTINGS_TABLE} WHERE {SETTINGS_KEY_COLUMN} = ?",
                        (SETTINGS_DOMAIN,)).fetchone()


def read_settings(state, d=None):
    """(settings dict, "") or (None, reason)."""
    d = d or support_dir()
    try:
        with open(os.path.join(d, "orca-profile-index.json")) as f:
            index = json.load(f)
    except FileNotFoundError:
        return None, "no orca-profile-index.json"
    except (OSError, ValueError) as e:
        return None, f"orca-profile-index.json unreadable ({type(e).__name__})"
    pid = index.get("activeProfileId") if isinstance(index, dict) else None
    if not isinstance(pid, str) or not _PROFILE_ID.fullmatch(pid):
        return None, "orca-profile-index.json: activeProfileId missing or unusable"
    db = os.path.join(d, "profiles", pid, "profile-state.db")
    if not os.path.lexists(db):
        return None, "active profile has no profile-state.db"
    row, why = sqlite_ro.query(db, _settings_row, state)
    if why:
        return None, why
    if row is None:
        return None, f"no {SETTINGS_DOMAIN!r} row in {SETTINGS_TABLE}"
    try:
        settings = json.loads(row[0]) if isinstance(row[0], str) else None
    except ValueError:
        settings = None
    if not isinstance(settings, dict):
        return None, "settings payload is not a JSON object"
    return settings, ""


def _shown(v):
    return json.dumps(v)[:60]


def _permission(settings):
    v = settings.get("agentPermissionDefault")
    if isinstance(v, str) and v in MANUAL_VALUES:
        return Check("orca agent permissions", "ok", f"agentPermissionDefault is {_shown(v)}")
    if isinstance(v, str) and v in FULL_ACCESS_VALUES:
        return Check("orca agent permissions", "warn", f"{FULL_ACCESS_WARNING} (agentPermissionDefault is {_shown(v)})")
    return Check("orca agent permissions", "unknown", f"agentPermissionDefault is {_shown(v)}; not a value bw knows")


def _telemetry(settings):
    t = settings.get("telemetry")
    if isinstance(t, dict) and t.get("optedIn") is True:
        return Check("orca telemetry", "warn", "Orca telemetry is on (telemetry.optedIn)")
    return Check("orca telemetry", "ok", "telemetry not opted in")


def _managed_accounts(settings):
    n = 0
    for key in ("claudeManagedAccounts", "codexManagedAccounts"):
        v = settings.get(key, [])
        if not isinstance(v, list):
            return Check("orca managed accounts", "unknown", f"{key} is not a list")
        n += len(v)
    if n:
        return Check("orca managed accounts", "warn",
                     f"Orca manages {n} extra accounts; switching accounts under a running lead makes the logged plan wrong")
    return Check("orca managed accounts", "ok", "no managed accounts")


def check_settings(settings, reason):
    if settings is None:
        return [Check("orca settings", "unknown", reason)]
    hooks = "on" if settings.get("agentStatusHooksEnabled") is True else "off"
    return [_permission(settings), _telemetry(settings), _managed_accounts(settings),
            Check("orca status hooks", "ok", f"agent status hooks {hooks}")]
