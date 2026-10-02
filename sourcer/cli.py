from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from .config import load_config


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sourcer", description=__doc__)
    p.add_argument("--root", default=".", help="directory with profile.yaml etc.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="gather signals, poll boards, write + send the digest")
    r.add_argument("--no-send", action="store_true", help="write the report but don't notify")

    sub.add_parser("network", help="summarize your LinkedIn export (top companies)")

    t = sub.add_parser("probe", help="find the job board for a company name")
    t.add_argument("company")

    a = sub.add_parser("apply", help="tailor your resume + draft application answers for one posting")
    a.add_argument("url", nargs="?", default="", help="Greenhouse/Lever/Ashby/Workable posting URL")
    a.add_argument("--jd", help="file with the job description (for any other site)")
    a.add_argument("--company", default="", help="company name (with --jd)")
    a.add_argument("--title", default="", help="job title (with --jd)")
    a.add_argument("--questions", help="file of application questions, blank-line separated")
    a.add_argument("--variant", choices=["ds", "mle", "product"], help="force a base resume")

    rv = sub.add_parser("resume", help="render a base resume variant (sanity-check the bank)")
    rv.add_argument("--variant", choices=["ds", "mle", "product"], default="ds")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.root)
    os.environ.setdefault("SOURCER_RESUME", str(cfg.resume_path))

    if args.cmd == "run":
        from .digest import render
        from .notify import deliver
        from .pipeline import run
        res = run(cfg)
        md = render(res)
        cfg.reports_dir.mkdir(exist_ok=True)
        path = cfg.reports_dir / f"{datetime.now(timezone.utc):%Y-%m-%d-%H%M}.md"
        path.write_text(md)
        (cfg.reports_dir / "latest.md").write_text(md)
        sent = [] if args.no_send else deliver(res, md)
        print(f"{len(res.new_jobs)} new roles, {len(res.leads)} leads -> {path}"
              + (f" (sent: {', '.join(sent)})" if sent else ""))
        return 0

    if args.cmd == "network":
        from .linkedin import Network
        net = Network.load(cfg.connections_path, cfg.profile.get("warm_contacts"))
        if not net.count:
            print(f"No connections found at {cfg.connections_path}. See README for the export steps.")
            return 1
        ranked = sorted(net.by_company.items(), key=lambda kv: -len(kv[1]))
        print(f"{net.count} connections across {len(ranked)} companies. Top 30:")
        for _k, people in ranked[:30]:
            print(f"  {len(people):3d}  {people[0].company}")
        return 0

    if args.cmd == "probe":
        from .http import session
        from .sources.ats import PROVIDERS, discover_board
        found = discover_board(session(), args.company, list(PROVIDERS))
        if not found:
            print("No Greenhouse/Lever/Ashby/Workable board found. Add ats+slug to watchlist.yaml manually.")
            return 1
        ats, slug, jobs = found
        print(f"{ats}/{slug}: {len(jobs)} open roles")
        for j in jobs[:20]:
            print(f"  - {j.title} ({j.location})")
        return 0

    if args.cmd in ("apply", "resume"):
        from .resume import Bank
        if not cfg.resume_path.exists():
            print(f"No resume bank at {cfg.resume_path}. See README > Resume tailoring.")
            return 1
        bank = Bank.load(cfg.resume_path)

    if args.cmd == "resume":
        from .resume import base_resume, html_to_pdf, render_html
        out = cfg.applications_dir / "base"
        out.mkdir(parents=True, exist_ok=True)
        h = out / f"resume-{args.variant}.html"
        h.write_text(render_html(bank, base_resume(bank, args.variant)))
        pages = html_to_pdf(h, h.with_suffix(".pdf"))
        print(f"{h} ({pages or '?'} page(s))")
        return 0

    if args.cmd == "apply":
        from pathlib import Path

        from .models import Job
        from .sources.postings import fetch_posting, questions_from_text
        from .tailor import load_facts, prepare_application
        questions = []
        if args.jd:
            job = Job(company=args.company or "company", title=args.title or "role", url=args.url,
                      source="manual", external_id=args.url or args.jd,
                      description=Path(args.jd).read_text())
        elif args.url:
            got = fetch_posting(args.url)
            if not got:
                print("Couldn't fetch that posting. Save the description to a file and pass --jd.")
                return 1
            job, questions = got
        else:
            print("Pass a posting URL or --jd FILE.")
            return 1
        if args.questions:
            questions += questions_from_text(Path(args.questions).read_text())
        from . import llm
        if not llm.enabled():
            print("Note: ANTHROPIC_API_KEY not set - packet will use your base resume, no answers.")
        path = prepare_application(bank, job, questions, load_facts(cfg.answers_path),
                                   cfg.applications_dir, variant=args.variant)
        print(f"Packet: {path}")
        print((path / "application.md").read_text())
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
