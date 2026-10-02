"""Deliver the digest. Each channel is enabled by setting its env vars; all are optional."""
from __future__ import annotations

import logging
import os
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

log = logging.getLogger(__name__)


def _attachments(packets: list[str]) -> list[tuple[str, bytes]]:
    out = []
    for p in packets:
        d = Path(p)
        for name in ("resume.pdf", "application.md"):
            f = d / name
            if f.exists():
                out.append((f"{d.name}-{name}", f.read_bytes()))
    return out


def send_slack(md: str) -> bool:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        return False
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<\2|\1>", md)  # Slack mrkdwn links
    text = re.sub(r"^#+ (.*)$", r"*\1*", text, flags=re.M)
    requests.post(url, json={"text": text[:39000]}, timeout=20).raise_for_status()
    return True


def send_push(title: str, body: str) -> bool:
    """Phone push via ntfy.sh - install the ntfy app and subscribe to your (secret) topic."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    requests.post(f"https://ntfy.sh/{topic}", data=body.encode(), timeout=20,
                  headers={"Title": title, "Priority": "high", "Tags": "briefcase"}).raise_for_status()
    return True


def send_resend(subject: str, html_body: str, text: str, packets: list[str] | None = None) -> bool:
    """Resend (resend.com): free tier, one API key. Without a verified domain it can only
    send to the address you signed up with - which is exactly the digest use case."""
    key, to = os.environ.get("RESEND_API_KEY"), os.environ.get("DIGEST_EMAIL_TO")
    if not (key and to):
        return False
    import base64
    payload = {
        "from": os.environ.get("DIGEST_EMAIL_FROM", "Sourcer <onboarding@resend.dev>"),
        "to": [a.strip() for a in to.split(",")], "subject": subject, "html": html_body, "text": text,
    }
    att = _attachments(packets or [])
    if att:
        payload["attachments"] = [{"filename": n, "content": base64.b64encode(b).decode()} for n, b in att]
    r = requests.post("https://api.resend.com/emails", json=payload, timeout=30,
                      headers={"Authorization": f"Bearer {key}"})
    if not r.ok:
        raise RuntimeError(f"Resend {r.status_code}: {r.text[:300]}")
    return True


def send_smtp(subject: str, html_body: str, text: str, packets: list[str] | None = None) -> bool:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("DIGEST_EMAIL_TO")
    if not (host and to):
        return False
    user, pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    msg = MIMEMultipart("mixed")
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_FROM", user), to
    body = MIMEMultipart("alternative")
    body.attach(MIMEText(text, "plain"))
    body.attach(MIMEText(html_body, "html"))
    msg.attach(body)
    for filename, data in _attachments(packets or []):
        part = MIMEApplication(data, Name=filename)
        part["Content-Disposition"] = f'attachment; filename="{filename}"'
        msg.attach(part)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if user:
            s.login(user, pw)
        s.send_message(msg)
    return True


def email_configured() -> bool:
    return bool(os.environ.get("DIGEST_EMAIL_TO") and
                (os.environ.get("RESEND_API_KEY") or os.environ.get("SMTP_HOST")))


def deliver(res, md: str, *, always_email: bool = False) -> list[str]:
    """Email (Resend or SMTP) + optional Slack/push. The daily email goes out even on quiet
    days (always_email) so you know the system ran."""
    from .email_html import render_email
    sent = []
    if res.new_jobs or res.leads or always_email:
        subject, html_body, text = render_email(res)
        for name, fn in (("email", send_resend), ("email", send_smtp)):
            try:
                if fn(subject, html_body, text, res.packets):
                    sent.append(name)
                    break
            except Exception as e:  # noqa: BLE001 - report and try the next channel
                log.error("%s delivery failed: %s", fn.__name__, e)
    if not (res.new_jobs or res.leads):
        return sent
    try:
        if send_slack(md):
            sent.append("slack")
    except Exception as e:
        log.error("slack delivery failed: %s", e)
    top = res.new_jobs[:3]
    body = "\n".join(f"{sj.job.title} @ {sj.job.company} ({sj.score:.0f})" for sj in top) or \
        "\n".join(f"Reach out: {l.company}" for l in res.leads[:3])
    try:
        if send_push(f"Sourcer: {len(res.new_jobs)} new roles", body):
            sent.append("push")
    except Exception as e:
        log.error("push delivery failed: %s", e)
    return sent
