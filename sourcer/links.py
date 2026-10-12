"""One-click links that open prefilled actions in *your* Google account - no API access needed.

Useful when your account's admin blocks third-party apps (e.g. a university Workspace): the digest
links straight into a Gmail compose window or a new Calendar event in that account, and you hit
Send / Save yourself.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import urlencode


def account() -> str:
    """The Google account the links open in: SOURCER_GOOGLE_ACCOUNT, else the digest recipient."""
    acct = os.environ.get("SOURCER_GOOGLE_ACCOUNT") or os.environ.get("DIGEST_EMAIL_TO", "")
    return acct.split(",")[0].strip()


def gmail_compose(to: str, subject: str, body: str, acct: str | None = None) -> str:
    q = {"view": "cm", "fs": "1", "to": to, "su": subject, "body": body}
    acct = account() if acct is None else acct
    if acct:
        q["authuser"] = acct
    return "https://mail.google.com/mail/?" + urlencode(q)


def gcal_add(title: str, start: datetime, end: datetime, details: str = "", location: str = "",
             acct: str | None = None) -> str:
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    q = {"action": "TEMPLATE", "text": title, "dates": f"{fmt(start)}/{fmt(end)}", "details": details,
         "location": location}
    acct = account() if acct is None else acct
    if acct:
        q["authuser"] = acct
    return "https://calendar.google.com/calendar/render?" + urlencode(q)
