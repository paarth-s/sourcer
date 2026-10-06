"""The daily email: inline-styled HTML (what email clients render reliably) + plain-text fallback."""
from __future__ import annotations

import html
from datetime import datetime
from zoneinfo import ZoneInfo

from .pipeline import RunResult

TZ = ZoneInfo("America/Los_Angeles")
INK, MUTED, LINE, ACCENT, BG = "#1a1a1a", "#666666", "#e6e6e6", "#0b57d0", "#f6f7f9"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _e(s) -> str:
    return html.escape(str(s or ""))


def _money(v: float | None) -> str:
    if not v:
        return ""
    return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M" if v >= 1e6 else f"${v / 1e3:.0f}K"


def _age(dt: datetime | None, now: datetime) -> str:
    if not dt:
        return ""
    days = (now - dt).days
    return "posted today" if days <= 0 else "posted yesterday" if days == 1 else f"posted {days}d ago"


def _chip(text: str, strong: bool = False) -> str:
    bg, fg = ("#e8f0fe", ACCENT) if strong else ("#f1f3f4", "#444")
    return (f"<span style='display:inline-block;background:{bg};color:{fg};border-radius:10px;"
            f"padding:2px 8px;margin:2px 4px 2px 0;font-size:12px'>{_e(text)}</span>")


def _section(title: str, subtitle: str = "") -> str:
    sub = f"<div style='color:{MUTED};font-size:13px;margin-top:2px'>{_e(subtitle)}</div>" if subtitle else ""
    return (f"<tr><td style='padding:26px 0 8px'><div style='font-size:18px;font-weight:600;color:{INK}'>"
            f"{_e(title)}</div>{sub}</td></tr>")


def _card(inner: str) -> str:
    return (f"<tr><td style='padding:6px 0'><div style='border:1px solid {LINE};border-radius:8px;"
            f"padding:14px 16px;background:#fff'>{inner}</div></td></tr>")


def _link(url: str, text: str) -> str:
    return f"<a href='{_e(url)}' style='color:{ACCENT};text-decoration:none'>{_e(text)}</a>"


def _box(label: str, text: str) -> str:
    return (f"<div style='margin-top:6px'><div style='font-size:11px;color:{MUTED};text-transform:uppercase;"
            f"letter-spacing:.04em'>{_e(label)}</div><div style='margin-top:2px;padding:9px 11px;background:{BG};"
            f"border-radius:6px;font-size:13px;white-space:pre-wrap'>{_e(text)}</div></div>")


def _contacts_html(contacts: list) -> str:
    if not contacts:
        return ""
    out = [f"<div style='margin-top:12px;padding-top:10px;border-top:1px dashed {LINE}'>"
           f"<div style='font-size:13px;font-weight:600'>Who to contact</div>"]
    for c in contacts:
        if c.email:
            where = f"<a href='mailto:{_e(c.email)}' style='color:{ACCENT}'>{_e(c.email)}</a> (public email)"
        elif c.linkedin_url:
            where = _link(c.linkedin_url, "LinkedIn profile")
        else:
            where = _link(c.search_url, "find on LinkedIn") + " (profile not confirmed - check it's them)"
        tag = " &middot; 1st-degree connection" if c.first_degree else ""
        out.append(f"<div style='margin-top:10px;font-size:13px'><b>{_e(c.name)}</b>, {_e(c.title)}{tag}"
                   f"<div style='color:{MUTED}'>{_e(c.why)} &middot; {where}</div>")
        if c.email and c.email_body:
            out.append(_box(f"Email subject: {c.email_subject}", c.email_body))
        else:
            if c.connection_note and not c.first_degree:
                out.append(_box("LinkedIn connection note", c.connection_note))
            if c.message:
                out.append(_box("LinkedIn message" + ("" if c.first_degree else " (once they accept)"),
                                c.message))
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


def render_email(res: RunResult, now: datetime | None = None) -> tuple[str, str, str]:
    """Returns (subject, html, text)."""
    now = now or datetime.now(TZ)
    local = now.astimezone(TZ)
    n_jobs, n_leads = len(res.new_jobs), len(res.leads)
    if n_jobs or n_leads:
        subject = f"Sourcer {local:%b %d}: {n_jobs} new role{'s' * (n_jobs != 1)}, {n_leads} to reach out to"
    else:
        subject = f"Sourcer {local:%b %d}: nothing new today"
    rows: list[str] = []

    rows.append(
        f"<tr><td style='padding:8px 0 4px'><div style='font-size:22px;font-weight:700;color:{INK}'>"
        f"Your job sourcing digest</div><div style='color:{MUTED};font-size:13px;margin-top:4px'>"
        f"{local:%A, %B %d} &middot; {res.roles_scanned:,} roles scanned across {res.boards_polled} job boards"
        f"{f' &middot; {res.network_size:,} LinkedIn connections' if res.network_size else ''}</div></td></tr>")

    # --- Roles ---------------------------------------------------------------------
    rows.append(_section("Apply today", "New since yesterday, best fit first. Earlier applicants get read first."))
    if not res.new_jobs:
        rows.append(_card(f"<div style='color:{MUTED}'>No new roles above your threshold today.</div>"))
    for sj in res.new_jobs:
        j = sj.job
        meta = " &middot; ".join(x for x in (_e(j.company), _e(j.location), _age(j.posted_at, now)) if x)
        shown = [r for r in sj.reasons if not r.startswith("posted")]  # age is in the meta line
        reasons = "".join(_chip(r, strong=i == 0) for i, r in enumerate(shown))
        extra = []
        if sj.context:
            extra.append(f"<div style='color:{MUTED};font-size:13px;margin-top:6px'>{_e(sj.context)}</div>")
        if sj.funding:
            extra.append(f"<div style='font-size:13px;margin-top:6px'>Recent funding: "
                         f"{_link(sj.funding.url, sj.funding.headline)}</div>")
        if sj.llm_note:
            extra.append(f"<div style='font-size:13px;margin-top:6px'><b>Fit:</b> {_e(sj.llm_note)}</div>")
        for c in sj.connections[:3]:
            who = _link(c.url, c.name) if c.url else _e(c.name)
            extra.append(f"<div style='font-size:13px;margin-top:6px'>Ask for a referral: {who} "
                         f"<span style='color:{MUTED}'>({_e(c.position)})</span></div>")
        rows.append(_card(
            f"<table width='100%' cellpadding='0' cellspacing='0'><tr><td style='vertical-align:top'>"
            f"<div style='font-size:15px;font-weight:600'>{_link(j.url, j.title)}</div>"
            f"<div style='color:{MUTED};font-size:13px;margin-top:3px'>{meta}</div></td>"
            f"<td style='vertical-align:top;text-align:right;white-space:nowrap;padding-left:10px'>"
            f"<span style='font-size:13px;font-weight:600;color:{ACCENT}'>{sj.score:.0f}</span></td></tr></table>"
            f"<div style='margin-top:8px'>{reasons}</div>{''.join(extra)}{_contacts_html(sj.contacts)}"))

    # --- Outreach leads ---------------------------------------------------------------
    rows.append(_section("Reach out before the role exists",
                         "Recently funded, on-profile, no matching role posted yet."))
    if not res.leads:
        rows.append(_card(f"<div style='color:{MUTED}'>No new leads today.</div>"))
    for lead in res.leads:
        f = lead.funding
        tag = " &middot; ".join(x for x in (_e(f.round) if f and f.round else "",
                                            _money(f.amount_usd) if f else "") if x)
        tag_html = (f" <span style='color:{MUTED};font-weight:400;font-size:13px'>{tag}</span>"
                    if tag else "")
        body = [f"<div style='font-size:15px;font-weight:600;color:{INK}'>{_e(lead.company)}{tag_html}</div>"]
        if f:
            body.append(f"<div style='font-size:13px;margin-top:4px'>{_link(f.url, f.headline)}</div>")
        body.append("<div style='margin-top:8px'>" + "".join(_chip(r) for r in lead.reasons) + "</div>")
        for j in lead.adjacent_roles[:3]:
            body.append(f"<div style='font-size:13px;margin-top:6px'>Signal: hiring {_link(j.url, j.title)}</div>")
        for c in lead.connections[:3]:
            who = _link(c.url, c.name) if c.url else _e(c.name)
            body.append(f"<div style='font-size:13px;margin-top:6px'>You know {who} "
                        f"<span style='color:{MUTED}'>({_e(c.position)})</span></div>")
        if lead.contacts:
            body.append(_contacts_html(lead.contacts))
        else:
            links = " &middot; ".join(_link(u, k) for k, u in lead.search_links.items())
            body.append(f"<div style='font-size:13px;margin-top:8px'>Find people: {links}</div>")
        rows.append(_card("".join(body)))

    # --- Funding radar --------------------------------------------------------------
    others = [ev for ev in res.funding_seen if ev.company not in {l.company for l in res.leads}]
    if others:
        rows.append(_section("Also on your radar", "On-profile funding news."))
        def _amt(ev):
            return f" <span style='color:{MUTED}'>({_money(ev.amount_usd)})</span>" if ev.amount_usd else ""
        items = "".join(f"<div style='font-size:13px;padding:4px 0'>{_link(ev.url, ev.headline)}{_amt(ev)}</div>"
                        for ev in others[:12])
        rows.append(f"<tr><td>{items}</td></tr>")

    rows.append(
        f"<tr><td style='padding:28px 0 8px;color:{MUTED};font-size:12px;border-top:1px solid {LINE}'>"
        f"Sources: company job boards (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable), "
        f"funding news, Y Combinator ({res.yc_matches} on-profile startups tracked), HN Who's Hiring. "
        f"Tune what you see in profile.yaml and watchlist.yaml.</td></tr>")

    html_doc = (
        f"<!doctype html><html><body style='margin:0;padding:0;background:{BG}'>"
        f"<table width='100%' cellpadding='0' cellspacing='0' style='background:{BG}'><tr><td align='center' "
        f"style='padding:20px 12px'><table width='100%' cellpadding='0' cellspacing='0' "
        f"style='max-width:640px;font-family:{FONT};color:{INK};line-height:1.4'>"
        + "".join(rows) + "</table></td></tr></table></body></html>")

    text = [subject, ""]
    for sj in res.new_jobs:
        text.append(f"- {sj.job.title} @ {sj.job.company} ({sj.job.location}) [{sj.score:.0f}]\n  {sj.job.url}")
        for c in sj.contacts:
            text.append(f"    -> {c.name} ({c.title}) {c.email or c.linkedin_url}\n       {c.email_body or c.message}")
    if res.leads:
        text.append("\nReach out:")
        text += [f"- {l.company}: {l.funding.headline if l.funding else ''}" for l in res.leads]
    return subject, html_doc, "\n".join(text)
