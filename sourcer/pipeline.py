"""One sourcing run: gather signals -> discover job boards -> score -> alert."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import llm
from .config import Config
from .db import Store
from .linkedin import Network, people_search_links
from .models import FundingEvent, Job, OutreachLead, ScoredJob, normalize_company
from .scoring import company_fit, is_adjacent, score_job, title_match
from .http import session
from .sources import ats as ats_mod
from .sources.edgar import fetch_form_d
from .sources.funding import fetch_funding
from .sources.hn import fetch_hn_jobs
from .sources import workday
from .sources.yc import fetch_yc

log = logging.getLogger(__name__)

COMPANY_FIT_MIN = 18        # min company_fit() for a funded company to be tracked
FIRST_RUN_MAX_AGE_DAYS = 14  # on the very first run, only alert on postings this fresh


@dataclass
class RunResult:
    new_jobs: list[ScoredJob] = field(default_factory=list)
    leads: list[OutreachLead] = field(default_factory=list)
    funding_seen: list[FundingEvent] = field(default_factory=list)
    packets: list[str] = field(default_factory=list)
    yc_matches: int = 0
    roles_scanned: int = 0
    boards_polled: int = 0
    boards_discovered: int = 0
    network_size: int = 0
    errors: list[str] = field(default_factory=list)


def _register_companies(cfg: Config, store: Store, network: Network,
                        funded: list[FundingEvent], yc: list[dict] | None = None
                        ) -> list[tuple[str, str, str]]:
    """Record every company we should watch. Returns (key, name, origin) needing ATS discovery,
    in priority order."""
    to_probe: list[tuple[str, str, str]] = []
    reprobe = cfg.sources.get("ats", {}).get("reprobe_after_days", 7)

    def add(name: str, origin: str, ats: str | None = None, slug: str | None = None,
            note: str | None = None):
        key = normalize_company(name)
        if not key:
            return
        if ats and slug:
            store.upsert_company(key, name, origin, ats=ats, slug=slug, note=note)
            return
        if note:
            store.upsert_company(key, name, origin, note=note)
        if store.needs_probe(key, reprobe):
            store.upsert_company(key, name, origin)
            to_probe.append((key, name, origin))

    for c in cfg.watchlist:
        if c.get("workday"):
            add(c["name"], "watchlist", "workday", c["workday"], c.get("note"))
        else:
            add(c["name"], "watchlist", c.get("ats"), c.get("slug"), c.get("note"))
    for c in yc or []:
        add(c["name"], "yc", note=f"YC {c['batch']}: {c['one_liner']}".strip())
    for ev in funded:
        add(ev.company, "edgar" if ev.source == "sec-form-d" else "funding")
    for _key, name in network.companies().items():
        add(name, "connection")
    return to_probe


def run(cfg: Config, *, skip_network_fetch: bool = False, mark_seen: bool = True) -> RunResult:
    """One sourcing pass. With mark_seen=False the alerted roles/leads are not recorded, so the
    caller can call mark_delivered() only once the email actually went out."""
    res = RunResult()
    profile = cfg.profile
    store = Store(cfg.db_path)
    first_run = not store.has_any_jobs()
    network = Network.load(cfg.connections_path, profile.get("warm_contacts"))
    res.network_size = network.count
    now = datetime.now(timezone.utc)

    # 1. Funding signals ---------------------------------------------------------
    events: list[FundingEvent] = []
    if not skip_network_fetch:
        events += fetch_funding(cfg.sources)
        tracked = ([c["name"] for c in cfg.watchlist] + list(network.companies().values())
                   + [e.company for e in events])
        events += fetch_form_d(cfg.sources.get("edgar", {}), tracked)
    yc = fetch_yc(profile) if not skip_network_fetch and cfg.sources.get("yc", {}).get("enabled", True) else []
    res.yc_matches = len(yc)
    relevant_funded = []
    for ev in events:
        store.add_funding(ev)
        fit, _ = company_fit(ev, profile)
        # A Form D with industry details is a tech startup that just raised (see edgar.py):
        # worth scanning its job board even without on-profile keywords.
        stealth_raise = ev.source == "sec-form-d" and "Industry:" in ev.summary
        if fit >= COMPANY_FIT_MIN or network.at(ev.company) or stealth_raise:
            relevant_funded.append(ev)
    res.funding_seen = relevant_funded
    funding_by_key = store.recent_funding(days=120)

    # 2. Discover job boards --------------------------------------------------------
    s = session()
    ats_cfg = cfg.sources.get("ats", {})
    providers = ats_cfg.get("providers", list(ats_mod.PROVIDERS))
    max_probes = ats_cfg.get("max_probes_per_run", 80)
    to_probe = _register_companies(cfg, store, network, relevant_funded, yc)
    order = {"watchlist": 0, "funding": 1, "connection": 2, "yc": 3, "edgar": 4}
    to_probe.sort(key=lambda t: order.get(t[2], 9))
    for key, name, _origin in ([] if skip_network_fetch else to_probe[:max_probes]):
        found = ats_mod.discover_board(s, name, providers)
        if found:
            store.upsert_company(key, name, _origin, ats=found[0], slug=found[1], probed=True)
            res.boards_discovered += 1
        elif not ats_mod._THROTTLED:
            # Only record "no board" if every provider actually answered; otherwise retry next run.
            store.upsert_company(key, name, _origin, probed=True)
    store.conn.commit()

    # 3. Poll boards + HN --------------------------------------------------------
    all_jobs: list[Job] = []
    if not skip_network_fetch:
        def wanted(title: str) -> bool:
            return title_match(title, profile)[0] > 0 or is_adjacent(title)

        for row in store.boards():
            if row["ats"] == "workday":
                jobs = workday.fetch_jobs(s, row["slug"], row["name"], wanted)
            else:
                jobs = ats_mod.fetch_board(s, row["ats"], row["slug"], row["name"])
            if jobs is None:
                res.errors.append(f"{row['name']}: {row['ats']}/{row['slug']} board unavailable")
                continue
            res.boards_polled += 1
            all_jobs += jobs
        all_jobs += fetch_hn_jobs(cfg.sources.get("hn_whos_hiring", {}))

    res.roles_scanned = len(all_jobs)
    res.new_jobs, open_by_company = score_and_store(
        all_jobs, store, network, funding_by_key, profile, first_run=first_run, now=now)
    notes = store.notes()
    for sj in res.new_jobs:
        sj.context = notes.get(sj.job.company_key, "")

    # 4. Pre-emptive outreach leads -------------------------------------------------
    res.leads = build_leads(funding_by_key, open_by_company, network, store, profile, now=now)

    # 5. Optional LLM refinement --------------------------------------------------
    threshold = profile.get("alert_threshold", 45)
    llm.rerank(res.new_jobs, profile)
    res.new_jobs = sorted((j for j in res.new_jobs if j.score >= threshold), key=lambda x: -x.score)
    llm.draft_outreach(res.leads, profile)
    if not skip_network_fetch:
        res.packets = prepare_packets(cfg, res.new_jobs)

    store.close()
    if mark_seen:
        mark_delivered(cfg, res)
    return res


def score_and_store(jobs: list[Job], store: Store, network: Network,
                    funding_by_key: dict[str, FundingEvent], profile: dict, *,
                    first_run: bool, now: datetime):
    """Score every posting, persist it, and return (new alert-worthy jobs, jobs by company)."""
    threshold = profile.get("alert_threshold", 45)
    # Allow slack below threshold so the LLM pass can promote near-misses.
    candidate_floor = threshold - 10 if llm.enabled() else threshold
    new: list[ScoredJob] = []
    by_company: dict[str, list[Job]] = {}
    seen: set[str] = set()
    for job in jobs:
        if job.uid in seen:
            continue
        seen.add(job.uid)
        by_company.setdefault(job.company_key, []).append(job)
        conns = network.at(job.company)
        funding = funding_by_key.get(job.company_key)
        score, reasons = score_job(job, profile, n_connections=len(conns), funding=funding, now=now)
        is_new = store.upsert_job(job, score)
        if score < candidate_floor or store.was_alerted(job.uid):
            continue
        if not (is_new or first_run or store.is_pending(job.uid)):
            continue
        if first_run and job.posted_at and now - job.posted_at > timedelta(days=FIRST_RUN_MAX_AGE_DAYS):
            continue
        store.mark_pending(job.uid)  # queued for the next digest until delivery is confirmed
        new.append(ScoredJob(job=job, score=score, reasons=reasons, connections=conns, funding=funding))
    store.conn.commit()
    return new, by_company


def build_leads(funding_by_key: dict[str, FundingEvent], open_by_company: dict[str, list[Job]],
                network: Network, store: Store, profile: dict, *, now: datetime,
                max_age_days: int = 60) -> list[OutreachLead]:
    """Funded, on-profile companies with no matching role yet: reach out before the req opens."""
    leads = []
    for key, ev in funding_by_key.items():
        if ev.published and now - ev.published > timedelta(days=max_age_days):
            continue
        fit, reasons = company_fit(ev, profile)
        conns = network.at(ev.company)
        if conns:
            fit += 15
            reasons = reasons + [f"{len(conns)} connection(s) there"]
        if fit < COMPANY_FIT_MIN:
            continue
        jobs = open_by_company.get(key, [])
        if any(title_match(j.title, profile)[0] >= 40 for j in jobs):
            continue  # a matching role is already open - it'll show up as a job alert
        adjacent = [j for j in jobs if is_adjacent(j.title)]
        if adjacent:
            fit += 10
            reasons = reasons + ["hiring data infra now - DS role likely next"]
        if not store.lead_is_new(key):
            continue  # already surfaced in an earlier digest
        leads.append(OutreachLead(company=ev.company, score=fit, reasons=reasons, funding=ev,
                                  connections=conns, adjacent_roles=adjacent,
                                  search_links=people_search_links(ev.company)))
    leads.sort(key=lambda l: -l.score)
    return leads[:15]


def prepare_packets(cfg: Config, jobs: list[ScoredJob]) -> list[str]:
    """Tailored resume + drafted answers for the best new roles (needs Claude + a resume bank)."""
    settings = cfg.profile.get("applications") or {}
    top_n, min_score = settings.get("auto_prepare_top", 3), settings.get("min_score", 60)
    if not top_n or not llm.enabled() or not cfg.resume_path.exists():
        return []
    from .resume import Bank
    from .sources.postings import greenhouse_questions
    from .tailor import load_facts, prepare_application
    bank, facts = Bank.load(cfg.resume_path), load_facts(cfg.answers_path)
    paths = []
    for sj in [j for j in jobs if j.score >= min_score][:top_n]:
        job = sj.job
        questions = (greenhouse_questions(job.board, job.external_id)
                     if job.source == "greenhouse" and job.board else [])
        try:
            path = prepare_application(bank, job, questions, facts, cfg.applications_dir)
        except Exception as e:  # one bad posting shouldn't sink the run
            log.error("packet for %s failed: %s", job.uid, e)
            continue
        sj.packet = str(path)
        paths.append(str(path))
    return paths


def mark_delivered(cfg: Config, res: RunResult) -> None:
    """Record alerted roles and leads so they aren't repeated in the next digest."""
    store = Store(cfg.db_path)
    store.mark_alerted([sj.job.uid for sj in res.new_jobs])
    for lead in res.leads:
        store.mark_lead(normalize_company(lead.company))
    store.close()
