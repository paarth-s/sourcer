"""Hands-on helpers that need your Google account (same one-time `sourcer google-auth`):

- hold_events: puts the digest's events on your calendar as yellow, non-blocking
  "[Sourcer pick? keep/delete]" holds - keep = approve (RSVP via the link inside),
  delete = deny. Skips anything overlapping a meeting/interview/call with other people
  (30-min travel buffer), keeps one event per evening, never re-adds an event.
- draft_emails: every outreach email in the digest becomes a Gmail draft, ready to
  review and send. Sourcer never sends on your behalf.

Partiful/Luma RSVPs need your own login (phone number / Luma account), so the RSVP
itself stays a one-click link in the hold.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)
HOLD_PREFIX = "[Sourcer pick? keep/delete] "
PEOPLE = re.compile(r"interview|call|meeting|chat|coffee|1:1|sync|intro|screen|lunch|dinner|x paarth|<>|zoom|teams",
                    re.I)
BUFFER = timedelta(minutes=30)


def _when(e: dict, key: str) -> datetime | None:
    v = (e.get(key) or {}).get("dateTime")
    return datetime.fromisoformat(v) if v else None


def _with_people(e: dict) -> bool:
    """A commitment involving someone else (not a self-block like 'Coursework', not our holds)."""
    if (e.get("summary") or "").startswith(HOLD_PREFIX) or e.get("transparency") == "transparent":
        return False
    others = [a for a in e.get("attendees") or [] if not a.get("self") and not a.get("resource")]
    return bool(others) or bool(PEOPLE.search(e.get("summary") or ""))


def plan_holds(events: list, calendar: list[dict], already: set[str]) -> tuple[list, list[tuple[object, str]]]:
    """-> (events to hold, [(event, reason skipped)])."""
    busy = [(s, en, e.get("summary", "")) for e in calendar if _with_people(e)
            for s, en in [(_when(e, "start"), _when(e, "end"))] if s and en]
    picked, skipped, nights = [], [], set()
    for ev in events:
        if ev.uid in already:
            continue
        start, end = ev.start, ev.end or ev.start + timedelta(hours=2)
        clash = next((t for s, en, t in busy if s < end + BUFFER and en > start - BUFFER), None)
        if clash:
            skipped.append((ev, f"conflicts with '{clash}'"))
            continue
        night = start.astimezone(timezone(timedelta(hours=-8))).date()
        if night in nights:
            skipped.append((ev, "already holding another event that day"))
            continue
        nights.add(night)
        picked.append(ev)
    return picked, skipped


def hold_events(g, events: list, store, *, max_holds: int = 6) -> tuple[int, list[str]]:
    if not events:
        return 0, []
    now = datetime.now(timezone.utc)
    cal = g.calendar_events(now.isoformat(), (max(e.start for e in events) + timedelta(days=1)).isoformat())
    already = set((store.get_meta("events_held") or "").split("\n"))
    picked, skipped = plan_holds(events, cal, already)
    for ev in picked[:max_holds]:
        end = ev.end or ev.start + timedelta(hours=2)
        who = f" You know {', '.join(ev.known_hosts)} (hosting)." if ev.known_hosts else ""
        g.calendar_create({
            "summary": HOLD_PREFIX + ev.title,
            "start": {"dateTime": ev.start.isoformat()}, "end": {"dateTime": end.isoformat()},
            "location": f"{ev.venue} ({ev.url})", "transparency": "transparent", "colorId": "5",
            "description": (f"Suggested by Sourcer ({', '.join(ev.reasons[:3])}; {ev.guests} going).{who}\n\n"
                            f"APPROVE: keep this event and RSVP: {ev.url}\nDENY: delete this event.\n\n"
                            "Checked against your meetings and interviews - no conflicts.")})
        already.add(ev.uid)
    store.set_meta("events_held", "\n".join(x for x in list(already)[-2000:] if x))
    return min(len(picked), max_holds), [f"{e.title}: {why}" for e, why in skipped]


def draft_emails(g, res, store) -> int:
    done = set((store.get_meta("drafts_made") or "").split("\n"))
    n = 0
    for obj in list(res.new_jobs) + list(res.leads):
        for c in getattr(obj, "contacts", []) or []:
            if not (c.email and c.email_body):
                continue
            key = f"{c.email.lower()}|{c.email_subject}"
            if key in done:
                continue
            g.gmail_draft(c.email, c.email_subject, c.email_body)
            done.add(key)
            n += 1
    store.set_meta("drafts_made", "\n".join(x for x in list(done)[-2000:] if x))
    return n
