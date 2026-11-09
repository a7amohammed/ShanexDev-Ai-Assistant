# Security Policy - J.A.R.V.I.S. Mk. LV

## Secret handling

No credential is shipped with this project. Secrets are supplied through
environment variables (see `.env.example`) or entered in the in-app settings,
which store them locally in files excluded by `.gitignore`.

| Secret | Environment variable |
|---|---|
| Gemini API key | `GEMINI_API_KEY` |
| Spotify credentials | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` |
| Mail app-passwords | `JARVIS_MAIL_<ACCOUNT_ID>_PASSWORD` |

Environment values take precedence over stored values and are never written to disk.

## Credential rotation (required if you used an earlier build)

Any credential that was present in a previously distributed archive must be
considered compromised:

1. Revoke and regenerate the Gemini API key.
2. Regenerate the Spotify client secret and delete `config/.spotify_cache`.
3. Revoke and recreate every mail app-password.
4. Delete `config/certs/`; a new self-signed certificate is generated on first
   launch of the remote dashboard.
