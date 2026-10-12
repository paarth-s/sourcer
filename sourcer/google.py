"""Minimal Google client: Gmail (read-only) + Sheets, over plain REST with one OAuth refresh token.

One-time setup (run on your own computer - it opens a browser):
    python -m sourcer google-auth --client-secret path/to/client_secret.json
That prints GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN to save as
GitHub secrets. Nothing here ever logs message content or sheet values (Actions logs are public).
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets as _secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.compose",        # drafts only - Sourcer never sends
          "https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/calendar.events"]
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
CAL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"


def configured() -> bool:
    return all(os.environ.get(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"))


def sheet_id_from(url_or_id: str) -> str:
    m = re.search(r"/spreadsheets/d/([\w-]+)", url_or_id or "")
    return m.group(1) if m else (url_or_id or "").strip()


class Google:
    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self._token: str | None = None

    # --- auth -----------------------------------------------------------------------
    def _auth(self) -> dict:
        if not self._token:
            r = self.s.post(TOKEN_URL, data={
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
                "grant_type": "refresh_token"}, timeout=30)
            if not r.ok:
                raise RuntimeError(f"Google token refresh failed ({r.status_code}) - re-run `sourcer google-auth`")
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, url: str, **params) -> dict:
        r = self.s.get(url, params=params, headers=self._auth(), timeout=30)
        r.raise_for_status()
        return r.json()

    def _send(self, method: str, url: str, body: dict, **params) -> dict:
        r = self.s.request(method, url, params=params, json=body, headers=self._auth(), timeout=30)
        r.raise_for_status()
        return r.json()

    # --- Gmail ----------------------------------------------------------------------
    def gmail_search(self, query: str, limit: int = 300) -> list[str]:
        ids, token = [], None
        while len(ids) < limit:
            params = {"q": query, "maxResults": min(100, limit - len(ids))}
            if token:
                params["pageToken"] = token
            data = self._get(f"{GMAIL}/messages", **params)
            ids += [m["id"] for m in data.get("messages", [])]
            token = data.get("nextPageToken")
            if not token:
                break
        return ids

    def gmail_message(self, msg_id: str) -> dict:
        """-> {id, thread_id, date_ms, sender, to, subject, body} (body = plain text)."""
        m = self._get(f"{GMAIL}/messages/{msg_id}", format="full")
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        return {"id": m["id"], "thread_id": m.get("threadId"), "date_ms": int(m.get("internalDate", 0)),
                "sender": headers.get("from", ""), "to": headers.get("to", ""),
                "subject": headers.get("subject", ""), "body": _plain_text(m.get("payload", {})),
                "labels": m.get("labelIds", [])}

    def gmail_draft(self, to: str, subject: str, body: str) -> str:
        from email.mime.text import MIMEText
        m = MIMEText(body)
        m["To"], m["Subject"] = to, subject
        raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
        return self._send("POST", f"{GMAIL}/drafts", {"message": {"raw": raw}}).get("id", "")

    # --- Calendar -------------------------------------------------------------------
    def calendar_events(self, start_iso: str, end_iso: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"timeMin": start_iso, "timeMax": end_iso, "singleEvents": "true", "orderBy": "startTime",
                      "maxResults": 250}
            if token:
                params["pageToken"] = token
            data = self._get(CAL, **params)
            out += data.get("items", [])
            token = data.get("nextPageToken")
            if not token:
                return out

    def calendar_create(self, event: dict) -> dict:
        return self._send("POST", CAL, event)

    # --- Sheets ---------------------------------------------------------------------
    def sheet_tabs(self, sheet_id: str) -> list[str]:
        data = self._get(f"{SHEETS}/{sheet_id}", fields="sheets.properties.title")
        return [s["properties"]["title"] for s in data.get("sheets", [])]

    def sheet_values(self, sheet_id: str, rng: str) -> list[list[str]]:
        return self._get(f"{SHEETS}/{sheet_id}/values/{urllib.parse.quote(rng)}").get("values", [])

    def sheet_update(self, sheet_id: str, updates: list[tuple[str, list[list]]]) -> None:
        if updates:
            self._send("POST", f"{SHEETS}/{sheet_id}/values:batchUpdate", {
                "valueInputOption": "USER_ENTERED",
                "data": [{"range": rng, "values": vals} for rng, vals in updates]})

    def sheet_append(self, sheet_id: str, rng: str, rows: list[list]) -> None:
        if rows:
            self._send("POST", f"{SHEETS}/{sheet_id}/values/{urllib.parse.quote(rng)}:append",
                       {"values": rows}, valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS")

    def sheet_add_tab(self, sheet_id: str, title: str) -> None:
        self._send("POST", f"{SHEETS}/{sheet_id}:batchUpdate",
                   {"requests": [{"addSheet": {"properties": {"title": title}}}]})


def _plain_text(payload: dict) -> str:
    """Prefer text/plain parts; fall back to tag-stripped HTML."""
    plain, html = [], []

    def walk(p):
        mt, data = p.get("mimeType", ""), (p.get("body") or {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (plain if mt == "text/plain" else html if mt == "text/html" else []).append(text)
        for c in p.get("parts") or []:
            walk(c)
    walk(payload)
    if plain:
        return "\n".join(plain)
    text = re.sub(r"<(script|style).*?</\1>", " ", "\n".join(html), flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


# --- one-time local authorization ----------------------------------------------------------
def authorize(client_secret_path: str, port: int = 8765) -> dict:
    """Installed-app OAuth with a localhost redirect. Returns the three secrets to store."""
    cfg = json.load(open(client_secret_path))
    cfg = cfg.get("installed") or cfg.get("web") or cfg
    client_id, client_secret = cfg["client_id"], cfg["client_secret"]
    redirect = f"http://localhost:{port}/"
    state = _secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "state": state})
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Sourcer is authorized - you can close this tab.")

        def log_message(self, *a):
            pass

    print("Open this URL, sign in as the account with your Gmail + tracker sheet, and approve:\n\n" + url + "\n")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    srv = HTTPServer(("localhost", port), Handler)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    if got.get("error") or got.get("state") != state:
        raise RuntimeError(f"authorization failed: {got.get('error', 'state mismatch')}")
    r = requests.post(TOKEN_URL, data={"code": got["code"], "client_id": client_id,
                                       "client_secret": client_secret, "redirect_uri": redirect,
                                       "grant_type": "authorization_code"}, timeout=30)
    r.raise_for_status()
    tok = r.json()
    if "refresh_token" not in tok:
        raise RuntimeError("no refresh token returned - remove Sourcer's access at "
                           "myaccount.google.com/permissions and try again")
    return {"GOOGLE_CLIENT_ID": client_id, "GOOGLE_CLIENT_SECRET": client_secret,
            "GOOGLE_REFRESH_TOKEN": tok["refresh_token"]}
