from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
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
    r.add_argument("--daily", action="store_true",
                   help="scheduled mode: run + email once per day, from 6am America/Los_Angeles")
    r.add_argument("--force", action="store_true", help="with --daily: send even if already sent today")
    r.add_argument("--preview", action="store_true",
                   help="email the full current digest (all matching roles from the last 14 days, "
                        "not just unsent ones) without changing what future daily emails include")

    sub.add_parser("test-email", help="send a short test email to confirm delivery is configured")

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

    sub.add_parser("selftest", help="live check of every source, scoring and tailoring")

    ct = sub.add_parser("contacts", help="import a Mac Contacts .vcf export as warm contacts")
    ct.add_argument("vcf", help="path to the exported .vcf file")

    rv = sub.add_parser("resume", help="render a base resume variant (sanity-check the bank)")
    rv.add_argument("--variant", choices=["ds", "mle", "product"], default="ds")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.root)
    os.environ.setdefault("SOURCER_RESUME", str(cfg.resume_path))

    if args.cmd == "test-email":
        from .notify import email_configured, send_resend, send_smtp
        if not email_configured():
            print("Email not configured: set DIGEST_EMAIL_TO plus RESEND_API_KEY (or SMTP_HOST/USER/PASSWORD).")
            return 1
        subject = "Sourcer test email - delivery works"
        body = ("<p>If you can read this, Sourcer can email you. Your daily digest arrives around "
                "7&nbsp;AM Pacific.</p>")
        for fn in (send_resend, send_smtp):
            try:
                if fn(subject, body, "Sourcer can email you."):
                    print(f"sent via {fn.__name__}")
                    return 0
            except Exception as e:  # noqa: BLE001
                print(f"{fn.__name__} failed: {e}")
        return 1

    if args.cmd == "run":
        from zoneinfo import ZoneInfo

        from .db import Store
        from .digest import render
        from .email_html import render_email
        from .notify import deliver
        from .pipeline import run
        local = datetime.now(ZoneInfo("America/Los_Angeles"))
        today = f"{local:%Y-%m-%d}"
        if args.preview:
            return _preview(cfg)
        if args.daily and not args.force:
            store = Store(cfg.db_path)
            already = store.get_meta("last_daily_email") == today
            store.close()
            if already or local.hour < 6:
                print(f"daily: skipping ({'already sent today' if already else f'{local:%H:%M} PT is before 6am'})")
                return 0
        # Daily mode records what was alerted only after the email is delivered, so a failed
        # send doesn't silently drop roles from the next digest.
        res = run(cfg, mark_seen=not args.daily)
        md = render(res)
        cfg.reports_dir.mkdir(exist_ok=True)
        path = cfg.reports_dir / f"{datetime.now(timezone.utc):%Y-%m-%d-%H%M}.md"
        path.write_text(md)
        (cfg.reports_dir / "latest.md").write_text(md)
        (cfg.reports_dir / "latest-email.html").write_text(render_email(res)[1])
        sent = [] if args.no_send else deliver(res, md, always_email=args.daily)
        if args.daily and "email" in sent:
            from .pipeline import mark_delivered
            mark_delivered(cfg, res)
            store = Store(cfg.db_path)
            store.set_meta("last_daily_email", today)
            store.close()
        print(f"{len(res.new_jobs)} new roles, {len(res.leads)} leads, {res.roles_scanned} roles scanned"
              + (f" (sent: {', '.join(sent)})" if sent else " (not sent)"))
        if args.daily and "email" not in sent:
            print("daily: email was NOT sent - check RESEND_API_KEY / DIGEST_EMAIL_TO secrets")
            return 1
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

    if args.cmd == "contacts":
        from collections import Counter

        from .contacts import parse_vcards, write_connections_csv
        people = parse_vcards(Path(args.vcf).read_text(encoding="utf-8", errors="replace"))
        out = cfg.connections_path.with_name("Contacts.csv")
        write_connections_csv(people, out)
        top = Counter(p.company for p in people).most_common(25)
        print(f"{len(people)} contacts with a known company -> {out} (git-ignored)")
        for company, n in top:
            print(f"  {n:3d}  {company}")
        print("For scheduled runs: gzip -c data/Contacts.csv | base64 | gh secret set CONTACTS_B64")
        return 0

    if args.cmd == "selftest":
        from .selftest import run as selftest
        return selftest(cfg)

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


def _preview(cfg) -> int:
    """Run against a scratch copy of the state that keeps discovered job boards but forgets
    what was already sent, so the email shows everything currently matching."""
    import shutil
    import sqlite3
    import tempfile

    from .email_html import render_email
    from .notify import send_resend, send_smtp
    from .pipeline import run

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "preview.db"
        if cfg.db_path.exists():
            shutil.copy(cfg.db_path, scratch)
            con = sqlite3.connect(scratch)
            con.executescript("DELETE FROM jobs; DELETE FROM leads;")
            con.commit()
            con.close()
        os.environ["SOURCER_DB"] = str(scratch)
        res = run(cfg, mark_seen=False)
        res.packets = []
        subject, html_body, text = render_email(res)
        subject = "[Preview] " + subject
        cfg.reports_dir.mkdir(exist_ok=True)
        (cfg.reports_dir / "preview-email.html").write_text(html_body)
        for fn in (send_resend, send_smtp):
            try:
                if fn(subject, html_body, text):
                    print(f"preview: {len(res.new_jobs)} roles, {len(res.leads)} leads, "
                          f"{res.roles_scanned} roles scanned (sent: email)")
                    return 0
            except Exception as e:  # noqa: BLE001
                print(f"{fn.__name__} failed: {e}")
        print(f"preview: {len(res.new_jobs)} roles, {len(res.leads)} leads (not sent - email not configured)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
