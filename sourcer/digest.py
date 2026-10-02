"""Render a run as Markdown (saved to reports/, sent by email/Slack)."""
from __future__ import annotations

from datetime import datetime, timezone

from .pipeline import RunResult


def _money(v: float | None) -> str:
    if not v:
        return ""
    return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M" if v >= 1e6 else f"${v:,.0f}"


def render(res: RunResult, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    out = [f"# Sourcer digest - {now:%a %b %d, %Y %H:%M} UTC", ""]
    out.append(f"{len(res.new_jobs)} new matching roles - {len(res.leads)} outreach leads - "
               f"{res.boards_polled} job boards polled ({res.boards_discovered} newly found) - "
               f"{res.network_size} LinkedIn connections loaded")
    out.append("")

    out.append("## New roles - apply early")
    if not res.new_jobs:
        out.append("_Nothing new above your threshold this run._")
    for sj in res.new_jobs:
        j = sj.job
        posted = f" - posted {j.posted_at:%b %d}" if j.posted_at else ""
        out.append(f"### [{j.title}]({j.url}) - {j.company}  `{sj.score:.0f}`")
        out.append(f"{j.location or 'location n/a'} - via {j.source}{posted}")
        if sj.reasons:
            out.append(f"- Why: {'; '.join(sj.reasons)}")
        if sj.llm_note:
            out.append(f"- Claude: {sj.llm_note}")
        if sj.funding:
            out.append(f"- Funding: [{sj.funding.headline}]({sj.funding.url})")
        for c in sj.connections[:3]:
            link = f"[{c.name}]({c.url})" if c.url else c.name
            out.append(f"- Ask for a referral: {link} - {c.position}")
        out.append("")

    out.append("## Reach out before the role exists")
    if not res.leads:
        out.append("_No new pre-emptive leads._")
    for lead in res.leads:
        f = lead.funding
        amt = f" - {_money(f.amount_usd)}" if f and f.amount_usd else ""
        rnd = f" {f.round}" if f and f.round else ""
        out.append(f"### {lead.company}{rnd}{amt}  `{lead.score:.0f}`")
        if f:
            out.append(f"[{f.headline}]({f.url})")
        out.append(f"- Why: {'; '.join(lead.reasons)}")
        for j in lead.adjacent_roles[:3]:
            out.append(f"- Signal: hiring [{j.title}]({j.url})")
        for c in lead.connections[:3]:
            link = f"[{c.name}]({c.url})" if c.url else c.name
            out.append(f"- You know: {link} - {c.position}")
        links = " - ".join(f"[{k}]({v})" for k, v in lead.search_links.items())
        out.append(f"- Find people: {links}")
        if lead.draft_message:
            out.append("")
            out.append("> " + lead.draft_message.replace("\n", "\n> "))
        out.append("")

    if res.funding_seen:
        out.append("## On-profile funding news this run")
        for ev in res.funding_seen[:25]:
            amt = f" ({_money(ev.amount_usd)})" if ev.amount_usd else ""
            out.append(f"- [{ev.headline}]({ev.url}){amt}")
        out.append("")

    if res.errors:
        out.append("<details><summary>Run warnings</summary>\n")
        out += [f"- {e}" for e in res.errors[:30]]
        out.append("\n</details>")
    return "\n".join(out) + "\n"
