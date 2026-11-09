"""Environment-variable overlay for J.A.R.V.I.S. Mk. LV.

Architecture role
-----------------
Every secret the system needs (Gemini key, Spotify application credentials,
mail app-passwords) can be supplied through environment variables. This module
is the single place that knows the variable names. Values found in the
environment always take precedence over values stored in ``config/*.json``.

The overlay is applied to *copies* of the configuration at read time only. It
is never written back to disk, so a secret supplied through the environment
cannot leak into a configuration file.
"""
from __future__ import annotations

import os
import re

GEMINI_ENV = "GEMINI_API_KEY"
SPOTIFY_ID_ENV = "SPOTIFY_CLIENT_ID"
SPOTIFY_SECRET_ENV = "SPOTIFY_CLIENT_SECRET"
MAIL_PASSWORD_ENV_TEMPLATE = "JARVIS_MAIL_{account_id}_PASSWORD"


def _env(name: str) -> str:
    """Return a stripped environment value, or an empty string when unset."""
    return (os.environ.get(name) or "").strip()


def mail_password_env_name(account_id: str) -> str:
    """Return the variable name holding the app-password of a mail account."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", account_id or "").strip("_").upper()
    return MAIL_PASSWORD_ENV_TEMPLATE.format(account_id=slug)


def mail_password_from_env(account_id: str) -> str:
    """Return the mail app-password supplied through the environment, if any."""
    return _env(mail_password_env_name(account_id))


def apply_env_overlay(config: dict) -> dict:
    """Return a copy of ``config`` with environment-supplied secrets applied."""
    merged = dict(config or {})

    gemini = _env(GEMINI_ENV)
    if gemini:
        merged["gemini_api_key"] = gemini

    spotify_id = _env(SPOTIFY_ID_ENV)
    spotify_secret = _env(SPOTIFY_SECRET_ENV)
    if spotify_id or spotify_secret:
        plugin_cfg = dict(merged.get("plugin_config") or {})
        spotify = dict(plugin_cfg.get("spotify") or {})
        if spotify_id:
            spotify["client_id"] = spotify_id
        if spotify_secret:
            spotify["client_secret"] = spotify_secret
        plugin_cfg["spotify"] = spotify
        merged["plugin_config"] = plugin_cfg

    return merged
