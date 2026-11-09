"""
core/mail_store.py — the list of mail accounts Jarvis can use.

WHERE THINGS LIVE
    config/mail_accounts.json   account list: label, address, servers. No secrets
                                when the OS keyring is available.
    OS keyring (service "jarvis-mail")
                                the password / app-password of each account.
                                Windows Credential Manager, macOS Keychain or
                                Secret Service on Linux — via `pip install keyring`.

    If `keyring` is not installed the password falls back to the JSON file, which
    is the same trust level as the Gemini key in api_keys.json. The settings panel
    tells the user which of the two is in effect.

NOTHING HERE TALKS TO A MAIL SERVER — see core/mail_client.py.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from memory.config_manager import CONFIG_DIR, ensure_config_dir, load_api_keys, _patch_config

ACCOUNTS_FILE = CONFIG_DIR / "mail_accounts.json"
KEYRING_SERVICE = "jarvis-mail"
_lock = threading.Lock()

# provider -> connection defaults. "other" means the user types the hosts in.
PROVIDERS: dict[str, dict] = {
    "gmail": {
        "name": "Gmail / Google Workspace",
        "imap_host": "imap.gmail.com", "imap_port": 993,
        "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl",
        "hint": "16-letter app password (myaccount.google.com/apppasswords). "
                "Requires two-step verification.",
    },
    "icloud": {
        "name": "iCloud",
        "imap_host": "imap.mail.me.com", "imap_port": 993,
        "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_security": "starttls",
        "hint": "App-specific password (appleid.apple.com → Sign-In and Security). "
                "Your regular password will not work.",
    },
    "outlook": {
        "name": "Outlook / Hotmail",
        "imap_host": "outlook.office365.com", "imap_port": 993,
        "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_security": "starttls",
        "hint": "Microsoft has disabled basic authentication on many accounts; this may fail.",
    },
    "other": {
        "name": "Otro (IMAP/SMTP)",
        "imap_host": "", "imap_port": 993,
        "smtp_host": "", "smtp_port": 465, "smtp_security": "ssl",
        "hint": "Enter your provider's servers.",
    },
}

try:                                  # optional — never fatal
    import keyring as _keyring        # type: ignore
    _KEYRING_OK = True
except Exception:                     # pragma: no cover
    _keyring = None
    _KEYRING_OK = False


def keyring_available() -> bool:
    return _KEYRING_OK


# ── file helpers ────────────────────────────────────────────────────────────

def _read() -> dict:
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("accounts"), list):
            return data
    except Exception:
        pass
    return {"accounts": []}


def _write(data: dict) -> None:
    ensure_config_dir()
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(ACCOUNTS_FILE, 0o600)      # no-op on Windows, useful elsewhere
    except Exception:
        pass


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "cuenta"


# ── public API ──────────────────────────────────────────────────────────────

def list_accounts() -> list[dict]:
    """Accounts WITHOUT secrets — safe to hand to the UI or the model."""
    with _lock:
        out = []
        for a in _read()["accounts"]:
            b = {k: v for k, v in a.items() if k != "password"}
            out.append(b)
        return out


def get_account(ident: str) -> dict | None:
    """Find by id, label or address (case-insensitive, partial label ok)."""
    ident = (ident or "").strip().lower()
    if not ident:
        return None
    accts = list_accounts()
    for a in accts:
        if ident in (a["id"].lower(), a["label"].lower(), a["address"].lower()):
            return a
    hits = [a for a in accts if ident in a["label"].lower() or ident in a["id"].lower()]
    return hits[0] if len(hits) == 1 else None


def get_password(account_id: str) -> str:
    from config.env_overlay import mail_password_from_env
    env_pw = mail_password_from_env(account_id)
    if env_pw:                                   # environment variable wins
        return env_pw
    if _KEYRING_OK:
        try:
            pw = _keyring.get_password(KEYRING_SERVICE, account_id)
            if pw:
                return pw
        except Exception:
            pass
    with _lock:
        for a in _read()["accounts"]:
            if a["id"] == account_id:
                return a.get("password", "") or ""
    return ""


def add_account(label: str, provider: str, address: str, password: str,
                imap_host: str = "", imap_port: int = 0,
                smtp_host: str = "", smtp_port: int = 0,
                smtp_security: str = "") -> dict:
    """Create (or replace, if the label already exists) an account. Returns it
    without the password. Raises ValueError with a message fit for the UI."""
    label = (label or "").strip()
    address = (address or "").strip()
    password = (password or "").strip().replace(" ", "")   # Google shows app passwords in 4 groups
    provider = provider if provider in PROVIDERS else "other"
    if not label:
        raise ValueError("Give the account a name (for example \"School\").")
    if "@" not in address:
        raise ValueError("The email address does not look valid.")
    if not password:
        raise ValueError("The app password is missing.")

    d = PROVIDERS[provider]
    rec = {
        "label": label,
        "provider": provider,
        "address": address,
        "imap_host": (imap_host or d["imap_host"]).strip(),
        "imap_port": int(imap_port or d["imap_port"]),
        "smtp_host": (smtp_host or d["smtp_host"]).strip(),
        "smtp_port": int(smtp_port or d["smtp_port"]),
        "smtp_security": (smtp_security or d["smtp_security"]),
    }
    if not rec["imap_host"] or not rec["smtp_host"]:
        raise ValueError("Faltan los servidores IMAP/SMTP.")

    with _lock:
        data = _read()
        existing = next((a for a in data["accounts"]
                         if a["label"].lower() == label.lower()), None)
        if existing:
            rec["id"] = existing["id"]
            data["accounts"] = [rec if a["id"] == existing["id"] else a for a in data["accounts"]]
        else:
            base, n, ids = _slug(label), 2, {a["id"] for a in data["accounts"]}
            rec["id"] = base
            while rec["id"] in ids:
                rec["id"] = f"{base}-{n}"; n += 1
            data["accounts"].append(rec)

        stored_in_keyring = False
        if _KEYRING_OK:
            try:
                _keyring.set_password(KEYRING_SERVICE, rec["id"], password)
                stored_in_keyring = True
            except Exception:
                stored_in_keyring = False
        if not stored_in_keyring:
            for a in data["accounts"]:
                if a["id"] == rec["id"]:
                    a["password"] = password
        else:
            for a in data["accounts"]:
                if a["id"] == rec["id"]:
                    a.pop("password", None)
        _write(data)
    return {k: v for k, v in rec.items() if k != "password"}


def remove_account(account_id: str) -> bool:
    """Delete the account and its stored password. True if something was removed."""
    with _lock:
        data = _read()
        before = len(data["accounts"])
        data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
        removed = len(data["accounts"]) < before
        if removed:
            _write(data)
    if _KEYRING_OK:
        try:
            _keyring.delete_password(KEYRING_SERVICE, account_id)
        except Exception:
            pass
    return removed


# ── briefing switch ─────────────────────────────────────────────────────────

def get_brief_mail_enabled() -> bool:
    """Whether the morning briefing includes unread mail. On by default, and
    only ever acts when at least one account exists."""
    return bool(load_api_keys().get("mail_in_brief", True))


def save_brief_mail_enabled(enabled: bool) -> None:
    _patch_config(mail_in_brief=bool(enabled))
