"""In-person events worth going to: Luma (SF discovery feed) + Partiful (SF explore page).

Both are public, no login: Luma's discovery API returns events with hosts and guest counts;
Partiful's explore page embeds its event list as JSON. Events are kept when they match your
profile (data/ML, your domains, startup/hiring/networking) and are in the Bay Area or online.
Someone from your network hosting or featured is called out - that's a warm introduction.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)

LUMA_API = "https://api.lu.ma/discover/get-paginated-events"
LUMA_SF = "discplace-BDj7GNbGlsF7Cka"           # Luma's San Francisco discovery place
SF_LAT, SF_LON = 37.7749, -122.4194
PARTIFUL_EXPLORE = "https://partiful.com/explore/{city}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; sourcer)"}

# keyword -> points. Matched on title + organizer + host bios + description.
DEFAULT_KEYWORDS = {
    # your craft
    "data science": 5, "data scientist": 5, "data scientists": 5, "data engineering": 3, "machine learning": 4, "ml": 3, "applied ai": 3, "llm": 2,
    "analytics": 3, "experimentation": 4, "a/b test": 4, "causal": 4, "recommendation": 4, "recsys": 4,
    "personalization": 4, "ranking": 3, "search": 2, "pricing": 4, "forecasting": 3, "statistics": 3,
    "ai": 1, "agents": 1, "data": 2,
    # your domains
    "proptech": 5, "real estate": 5, "travel": 3, "airline": 5, "hospitality": 3, "marketplace": 3,
    # career leverage
    "hiring": 4, "recruiting": 3, "career": 3, "careers": 3, "job": 2, "jobs": 2, "networking": 2, "founders": 1,
    "founder": 1, "startup": 1, "demo day": 2, "vc": 1, "investors": 0.5, "meetup": 2, "happy hour": 1, "mixer": 1,
    "hackathon": 2, "fireside": 2, "panel": 1, "tech talk": 3, "workshop": 1,
}
CORE = {k for k in DEFAULT_KEYWORDS if k not in {"ai", "agents", "llm", "data", "founder", "founders", "startup",
                                                  "vc", "investors", "happy hour", "mixer", "panel", "workshop",
                                                  "hackathon", "job", "search"}}
NEGATIVE = re.compile(r"\b(yoga|meditation|dj|rave|comedy|karaoke|drag|singles|dating|bachelor|kids|wine tasting|"
                      r"sound bath|run club|pilates|art walk|flea|market day|halloween party|church|bible)\b", re.I)
BAY_AREA = re.compile(r"san francisco|\bsf\b|oakland|berkeley|emeryville|palo alto|menlo park|mountain view|"
                      r"sunnyvale|santa clara|san jose|san mateo|redwood city|burlingame|south san francisco|"
                      r"cupertino|los altos|foster city|alameda|daly city|sausalito", re.I)


@dataclass
class Event:
    title: str
    url: str
    start: datetime
    venue: str
    organizer: str
    source: str                     # luma | partiful
    guests: int = 0
    online: bool = False
    hosts: list[str] = field(default_factory=list)
    text: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    known_hosts: list[str] = field(default_factory=list)
    end: datetime | None = None

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.url}"


def _dt(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_luma(s: requests.Session, place: str = LUMA_SF, pages: int = 4, days: int = 21) -> list[Event]:
    """SF featured feed + the Tech and AI categories near SF (lat/long), deduped."""
    feeds = [{"discover_place_api_id": place}] + [
        {"category_api_id": c, "latitude": SF_LAT, "longitude": SF_LON} for c in ("cat-tech", "cat-ai")]
    out: dict[str, Event] = {}
    for f in feeds:
        for e in _luma_feed(s, f, pages, days):
            out.setdefault(e.url, e)
    return list(out.values())


def _luma_feed(s: requests.Session, base: dict, pages: int, days: int) -> list[Event]:
    out, cursor, horizon = [], None, datetime.now(timezone.utc) + timedelta(days=days)
    for _ in range(pages):
        params = {**base, "pagination_limit": 50}
        if cursor:
            params["pagination_cursor"] = cursor
        try:
            r = s.get(LUMA_API, params=params, headers=UA, timeout=(10, 30))
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("luma: %s", type(e).__name__)
            break
        for x in data.get("entries", []):
            ev, cal = x.get("event") or {}, x.get("calendar") or {}
            start = _dt(ev.get("start_at"))
            if not start or not ev.get("url"):
                continue
            geo = ev.get("geo_address_info") or {}
            people = (x.get("hosts") or []) + (x.get("featured_guests") or [])
            out.append(Event(
                title=ev.get("name", ""), url=f"https://luma.com/{ev['url']}", start=start,
                venue=geo.get("city_state") or geo.get("short_address") or ("Online" if ev.get("location_type") ==
                                                                             "online" else ""),
                organizer=cal.get("name", ""), source="luma", guests=x.get("guest_count") or 0,
                end=_dt(ev.get("end_at")),
                online=ev.get("location_type") == "online",
                hosts=[p.get("name", "") for p in people if p.get("name")],
                text=" ".join([cal.get("description_short") or ""] + [p.get("bio_short") or "" for p in people])))
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor or (out and out[-1].start > horizon):
            break
    return [e for e in out if e.start <= horizon]


def fetch_partiful(s: requests.Session, city: str = "sf") -> list[Event]:
    try:
        r = s.get(PARTIFUL_EXPLORE.format(city=city), headers=UA, timeout=(10, 30))
        r.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        pp = json.loads(m.group(1))["props"]["pageProps"] if m else {}
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("partiful: %s", type(e).__name__)
        return []
    items = list(pp.get("feedItems") or []) + list((pp.get("trendingSection") or {}).get("items") or [])
    for sec in pp.get("sections") or []:
        items += sec.get("items") or []
    out, seen = [], set()
    for it in items:
        ev = it.get("event") or {}
        if not ev.get("id") or ev["id"] in seen or not ev.get("isPublic", True):
            continue
        seen.add(ev["id"])
        start = _dt(ev.get("startDate"))
        if not start:
            continue
        loc = ev.get("locationInfo") or {}
        maps = loc.get("mapsInfo") or {}
        out.append(Event(
            title=ev.get("title", ""), url=f"https://partiful.com/e/{ev['id']}", start=start,
            venue=", ".join(filter(None, [maps.get("name"), maps.get("approximateLocation")])) or
            loc.get("neighborhood", ""), organizer="", source="partiful", end=_dt(ev.get("endDate")),
            guests=(ev.get("goingGuestCount") or 0) + (ev.get("approvedGuestCount") or 0),
            text=f"{ev.get('description') or ''} {' '.join(map(str, it.get('tags') or []))}"))
    return out


def _has(text: str, kw: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text) is not None


def score_events(events: list[Event], profile: dict, known_people: set[str] | None = None,
                 now: datetime | None = None, min_score: float | None = None) -> list[Event]:
    cfg = profile.get("events") or {}
    kw = {**DEFAULT_KEYWORDS, **(cfg.get("keywords") or {})}
    floor = cfg.get("min_score", 6) if min_score is None else min_score
    now = now or datetime.now(timezone.utc)
    known = {n.lower() for n in (known_people or set()) if len(n.split()) >= 2}
    keep, seen_titles = [], set()
    for e in events:
        if e.start < now or e.start > now + timedelta(days=cfg.get("days_ahead", 21)):
            continue
        where = f"{e.venue} {e.text}"
        if not e.online and not BAY_AREA.search(where) and e.source != "partiful":
            continue
        title, org, rest = e.title.lower(), e.organizer.lower(), e.text.lower()
        if NEGATIVE.search(e.title):
            continue
        t_hits = [k for k in kw if _has(title, k)]
        o_hits = [k for k in kw if _has(org, k) and k not in t_hits]
        r_hits = [k for k in kw if _has(rest, k) and k not in t_hits and k not in o_hits]
        # The event itself (title/organizer) must be about your craft, domain or career - not just its hosts' bios.
        if not (t_hits or o_hits):
            continue
        score = 2 * sum(kw[k] for k in t_hits) + sum(kw[k] for k in o_hits) + 0.5 * min(sum(kw[k] for k in r_hits), 6)
        hits = t_hits + o_hits + r_hits
        e.known_hosts = [h for h in e.hosts if h.lower() in known]
        if e.known_hosts:
            score += 8
        if e.guests >= 100:
            score += 2
        if e.online:
            score -= 3                      # in person is where intros happen
        e.score, e.reasons = score, sorted(set(t_hits + o_hits) or set(hits), key=lambda k: -kw[k])[:4]
        key = re.sub(r"\W+", "", e.title.lower())[:40]
        if score >= floor and key not in seen_titles:
            seen_titles.add(key)
            keep.append(e)
    return sorted(keep, key=lambda e: -e.score)


def fetch_events(s: requests.Session, profile: dict) -> list[Event]:
    cfg = profile.get("events") or {}
    if cfg.get("enabled") is False:
        return []
    out = []
    if cfg.get("luma", True):
        out += fetch_luma(s, pages=cfg.get("luma_pages", 4), days=cfg.get("days_ahead", 21))
    if cfg.get("partiful", True):
        out += fetch_partiful(s, cfg.get("partiful_city", "sf"))
    return out
