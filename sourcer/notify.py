"""Deliver the digest. Each channel is enabled by setting its env vars; all are optional."""
from __future__ import annotations

import logging
import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

log = logging.getLogger(__name__)


def _md_to_html(md: str) -> str:
    """Tiny Markdown -> HTML good enough for an email digest."""
    out = []
    for line in md.splitlines():
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', line)
        line = re.sub(r"`([^`]+)`", r"<b>[\1]</b>", line)
        line = re.sub(r"_([^_]+)_$", r"<i>\1</i>", line)
        if line.startswith("### "):
            out.append(f"<h3 style='margin:14px 0 2px'>{line[4:]}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2 style='border-bottom:1px solid #ddd'>{line[3:]}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("- "):
            out.append(f"<div style='margin-left:12px'>&bull; {line[2:]}</div>")
        elif line.startswith("> "):
            out.append(f"<blockquote style='color:#444;border-left:3px solid #ccc;padding-left:8px'>{line[2:]}</blockquote>")
        elif line.strip():
            out.append(f"<div>{line}</div>")
    return "<div style='font-family:sans-serif;max-width:760px'>" + "\n".join(out) + "</div>"


def send_email(subject: str, md: str) -> bool:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("DIGEST_EMAIL_TO")
    if not (host and to):
        return False
    user, pw = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("SMTP_FROM", user), to
    msg.attach(MIMEText(md, "plain"))
    msg.attach(MIMEText(_md_to_html(md), "html"))
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if user:
            s.login(user, pw)
        s.send_message(msg)
    return True


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


def deliver(res, md: str) -> list[str]:
    if not res.new_jobs and not res.leads:
        return []
    subject = f"Sourcer: {len(res.new_jobs)} new roles, {len(res.leads)} outreach leads"
    sent = []
    for name, fn in (("email", lambda: send_email(subject, md)), ("slack", lambda: send_slack(md))):
        try:
            if fn():
                sent.append(name)
        except Exception as e:  # one broken channel shouldn't sink the others
            log.error("%s delivery failed: %s", name, e)
    top = res.new_jobs[:3]
    body = "\n".join(f"{sj.job.title} @ {sj.job.company} ({sj.score:.0f})" for sj in top) or \
        "\n".join(f"Reach out: {l.company}" for l in res.leads[:3])
    try:
        if send_push(subject, body):
            sent.append("push")
    except Exception as e:
        log.error("push delivery failed: %s", e)
    return sent
