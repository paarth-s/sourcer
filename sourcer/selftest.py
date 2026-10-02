"""Live smoke test of every source + scoring + tailoring. Prints only public data.

Uses the fictional tests/fixtures/resume.yaml for tailoring so it is safe to run where
logs are public (e.g. GitHub Actions on a public repo).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .config import Config
from .http import sec_user_agent, session
from .scoring import score_job
from .sources import ats, edgar, hn
from .sources.funding import fetch_funding
from .sources.postings import greenhouse_questions

# Boards known to exist on each ATS (public careers pages).
KNOWN = [("greenhouse", "airbnb", "Airbnb"), ("lever", "palantir", "Palantir"),
         ("ashby", "ramp", "Ramp"), ("workable", "huggingface", "Hugging Face")]


def _line(ok: bool, name: str, detail: str) -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def run(cfg: Config) -> int:
    s = session()
    results, all_jobs = [], []
    t0 = time.time()

    print("== Job boards")
    for provider, slug, name in KNOWN:
        jobs = ats.fetch_board(s, provider, slug, name)
        ok = bool(jobs)
        results.append(_line(ok, f"{provider}/{slug}", f"{len(jobs or [])} open roles"
                             + (f", e.g. '{jobs[0].title}'" if jobs else "")))
        all_jobs += jobs or []

    found = ats.discover_board(s, "Airbnb", list(ats.PROVIDERS))
    results.append(_line(bool(found), "board discovery ('Airbnb')",
                         f"{found[0]}/{found[1]}" if found else "not found"))

    print("\n== Funding news")
    events = fetch_funding({"google_news_queries": cfg.sources.get("google_news_queries", [])[:3],
                            "rss_feeds": cfg.sources.get("rss_feeds", [])[:2]})
    results.append(_line(bool(events), "funding RSS", f"{len(events)} funding announcements parsed"))
    for ev in events[:8]:
        amt = f"${ev.amount_usd / 1e6:.1f}M" if ev.amount_usd else "?"
        print(f"    - {ev.company} | {amt} | {ev.round or '?'} | {ev.headline[:90]}")

    print("\n== SEC Form D")
    ua = sec_user_agent(cfg.sources.get("edgar", {}).get("user_agent", ""))
    from datetime import date, timedelta
    end = date.today()
    r = s.get(edgar.SEARCH, timeout=30, headers={"User-Agent": ua}, params={
        "q": '"software"', "forms": "D", "dateRange": "custom",
        "startdt": (end - timedelta(days=14)).isoformat(), "enddt": end.isoformat()})
    print(f"    HTTP {r.status_code}, user-agent {'from SEC_USER_AGENT' if os.environ.get('SEC_USER_AGENT') else 'default (no contact - SEC may reject)'}")
    try:
        raw = r.json()
        hits = raw.get("hits", {}).get("hits", [])
        print(f"    raw hits: {len(hits)} (total {raw.get('hits', {}).get('total')}); top-level keys {list(raw)[:6]}")
        if hits:
            print(f"    first hit keys: {list(hits[0])}; _source keys: {list(hits[0].get('_source', {}))[:12]}")
            print(f"    first names: {[h.get('_source', {}).get('display_names') for h in hits[:3]]}")
    except ValueError:
        raw = None
        print(f"    non-JSON body: {r.text[:200]!r}")
    filings = edgar.parse_search(raw) if raw else []
    results.append(_line(bool(filings), "EDGAR full-text search", f"{len(filings)} operating-company filings"))
    for ev in filings[:5]:
        print(f"    - {ev.headline}")

    print("\n== HN Who's Hiring")
    hn_jobs = hn.fetch_hn_jobs({"enabled": True})
    results.append(_line(bool(hn_jobs), "HN thread", f"{len(hn_jobs)} postings"))
    all_jobs += hn_jobs

    print("\n== Scoring against profile.yaml (top 10 of all fetched roles)")
    scored = sorted(((score_job(j, cfg.profile)[0], j) for j in all_jobs), key=lambda x: -x[0])
    for sc, j in scored[:10]:
        print(f"    {sc:5.1f}  {j.title[:70]} @ {j.company} ({j.location[:30]})")
    matches = [x for x in scored if x[0] >= cfg.profile.get("alert_threshold", 45)]
    results.append(_line(True, "scoring", f"{len(matches)} of {len(all_jobs)} roles above threshold"))

    print("\n== Greenhouse application questions")
    gh = next((j for _, j in scored if j.source == "greenhouse"), None)
    if gh:
        qs = greenhouse_questions(gh.board, gh.external_id)
        results.append(_line(bool(qs), f"questions for '{gh.title}'", f"{len(qs)} questions"))
        for qq in qs[:12]:
            print(f"    - [{qq.type}{', EEO-skip' if qq.is_eeo else ''}] {qq.label}")

    print("\n== Tailoring (fictional fixture resume)")
    from . import llm
    if not llm.enabled():
        print("[SKIP] ANTHROPIC_API_KEY not set")
    elif scored:
        from .resume import Bank
        from .tailor import tailor_resume
        bank = Bank.load(Path(__file__).parent.parent / "tests" / "fixtures" / "resume.yaml")
        job = scored[0][1]
        t = tailor_resume(bank, job)
        ok = not any("failed" in w for w in t.warnings)
        results.append(_line(ok, f"tailor to '{job.title}' @ {job.company}",
                             f"variant={t.resume.variant}, {len(t.changes)} changes, "
                             f"{len(t.warnings)} guardrail reverts"))
        print(f"    objective: {t.resume.summary}")
        for _r, title, bullets in t.resume.roles:
            print(f"    {title}")
            for b in bullets:
                print(f"      - {b}")
        for w in t.warnings:
            print(f"    ! {w}")

    print(f"\n{sum(results)}/{len(results)} checks passed in {time.time() - t0:.0f}s")
    return 0 if all(results) else 1


if __name__ == "__main__":  # pragma: no cover
    from .config import load_config
    raise SystemExit(run(load_config(os.environ.get("SOURCER_ROOT", "."))))
