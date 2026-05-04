"""Shared Google OAuth2 helper for Calendar, Gmail, and Drive ingesters.

First run: opens a browser window for account authorization.
Tokens saved per account to ~/.jimmy/google_token_<sanitized_email>.json.
Subsequent runs: auto-refreshes silently.

Setup:
1. Go to console.cloud.google.com → New project
2. Enable Calendar API, Gmail API, and Drive API
3. Create OAuth 2.0 credentials (Desktop app type)
4. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env
"""
import json
import re
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from ..config import JIMMY_DATA_DIR

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

TOKEN_DIR = JIMMY_DATA_DIR


def _token_path(email: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9@._-]", "_", email)
    return TOKEN_DIR / f"google_token_{safe}.json"


def _list_saved_accounts() -> list[str]:
    """Return emails of all accounts with saved tokens."""
    accounts = []
    for p in TOKEN_DIR.glob("google_token_*.json"):
        try:
            data = json.loads(p.read_text())
            if email := data.get("email") or data.get("client_id"):
                accounts.append(email)
            else:
                # Fallback: derive from filename
                name = p.stem.replace("google_token_", "")
                accounts.append(name)
        except Exception:
            pass
    return accounts


def get_credentials(client_id: str, client_secret: str, account: str | None = None) -> Credentials:
    """Return valid Google OAuth credentials, prompting browser auth on first run.

    If account is None and there's exactly one saved token, uses that.
    If account is None and there are multiple saved tokens, asks user to pick.
    """
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)

    # Find which token file to use
    if account:
        token_path = _token_path(account)
    else:
        saved = list(TOKEN_DIR.glob("google_token_*.json"))
        if len(saved) == 1:
            token_path = saved[0]
        elif len(saved) > 1:
            print("Multiple Google accounts found:")
            for i, p in enumerate(saved):
                print(f"  [{i}] {p.name}")
            idx = int(input("Pick account number (or press Enter for 0): ").strip() or "0")
            token_path = saved[idx]
        else:
            # No saved tokens — will create new one
            token_path = _token_path("primary")

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_config = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://accounts.google.com/o/oauth2/token",
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(
                port=0,
                open_browser=True,
                authorization_prompt_message=(
                    "\n>>> Open this URL in your browser to authorize:\n{url}\n"
                ),
            )

        token_path.write_text(creds.to_json())
        print(f"Token saved to {token_path}")

    return creds


def get_all_credentials(client_id: str, client_secret: str) -> list[tuple[str, Credentials]]:
    """Return [(account_label, creds), ...] for all saved Google accounts."""
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    saved = list(TOKEN_DIR.glob("google_token_*.json"))
    for p in saved:
        try:
            creds = Credentials.from_authorized_user_file(str(p), SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                p.write_text(creds.to_json())
            label = p.stem.replace("google_token_", "")
            result.append((label, creds))
        except Exception as e:
            print(f"  Warning: could not load {p.name}: {e}")
    return result
