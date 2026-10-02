"""Rule-based fit scoring. Transparent and free; the optional LLM pass refines the top results."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import FundingEvent, Job

# Postings in these families aren't DS roles, but at a just-funded company they mean a
# data team is forming - a good moment to reach out before the DS req opens.
ADJACENT_TITLES = ["data engineer", "analytics engineer", "data analyst", "head of data",
                   "data platform", "ml infrastructure", "mlops"]


def _has(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text) is not None


def _hits(text: str, terms: list[str]) -> list[str]:
    text = text.lower()
    return [t for t in terms if _has(text, t)]


def title_match(title: str, profile: dict) -> tuple[float, list[str]]:
    t = title.lower()
    if _hits(t, profile.get("exclude_titles", [])):
        return 0.0, []
    tt = profile.get("target_titles", {})
    strong = _hits(t, tt.get("strong", []))
    if strong:
        return 40.0, [f"title: {strong[0]}"]
    moderate = _hits(t, tt.get("moderate", []))
    if moderate:
        return 20.0, [f"title: {moderate[0]}"]
    return 0.0, []


def is_adjacent(title: str) -> bool:
    return bool(_hits(title.lower(), ADJACENT_TITLES))


def domain_score(text: str, profile: dict) -> tuple[float, list[str]]:
    d = profile.get("domains", {})
    ml = _hits(text, d.get("ml_areas", []))
    vert = _hits(text, d.get("verticals", []))
    score = min(len(ml) * 8, 24) + min(len(vert) * 10, 20)
    reasons = []
    if ml:
        reasons.append("ML fit: " + ", ".join(ml[:4]))
    if vert:
        reasons.append("vertical: " + ", ".join(vert[:3]))
    return float(score), reasons


def location_excluded(location: str, profile: dict, title: str = "") -> bool:
    """e.g. "Remote - EMEA" or "Remote (Poland)" shouldn't count as remote for you."""
    return bool(_hits(f"{location} {title}".lower(), profile.get("exclude_locations") or []))


def location_ok(location: str, profile: dict, title: str = "") -> bool:
    if location_excluded(location, profile, title):
        return False
    prefs = profile.get("locations") or []
    if not prefs or not location:
        return True
    return any(p.lower() in location.lower() for p in prefs)


def score_job(job: Job, profile: dict, *, n_connections: int = 0,
              funding: FundingEvent | None = None,
              now: datetime | None = None) -> tuple[float, list[str]]:
    base, reasons = title_match(job.title, profile)
    if base == 0:
        return 0.0, []
    score = base

    dscore, dreasons = domain_score(f"{job.title} {job.description}", profile)
    score += dscore
    reasons += dreasons

    if funding is not None:
        fscore, _ = domain_score(f"{funding.headline} {funding.summary}", profile)
        score += 10 + min(fscore, 10)
        reasons.append(f"recently raised{' ' + funding.round if funding.round else ''}")

    if n_connections:
        score += 15 if n_connections == 1 else 20
        reasons.append(f"{n_connections} connection{'s' if n_connections > 1 else ''} there")

    if _hits(job.title.lower(), profile.get("seniority_bonus", [])):
        score += 5

    if location_excluded(job.location, profile, job.title):
        score -= 30
        reasons.append(f"location: {job.location}")
    elif not location_ok(job.location, profile):
        score -= 15
        reasons.append(f"location: {job.location}")

    now = now or datetime.now(timezone.utc)
    if job.posted_at:
        age_days = (now - job.posted_at).total_seconds() / 86400
        if age_days <= 2:
            score += 10
            reasons.append("posted <48h ago")
        elif age_days <= 7:
            score += 5
        elif age_days > 45:
            score -= 10
    return round(score, 1), reasons


def company_fit(funding: FundingEvent, profile: dict) -> tuple[float, list[str]]:
    """How interesting is a newly-funded company, before we've seen any roles?"""
    score, reasons = domain_score(f"{funding.company} {funding.headline} {funding.summary}", profile)
    r = (funding.round or "").lower()
    if r in ("seed", "pre-seed", "pre-series a", "series a", "series b"):
        score += 10
        reasons.append(f"{funding.round} stage - building first data team")
    elif r.startswith("series"):
        score += 5
    elif funding.amount_usd and 1e6 <= funding.amount_usd <= 150e6:
        score += 8  # round not named in the headline, but a startup-sized raise
        reasons.append("early-stage raise")
    if funding.amount_usd and funding.amount_usd >= 10e6:
        score += 5
    return score, reasons
