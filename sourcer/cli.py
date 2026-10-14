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
                   help="scheduled mode: run overnight + email once per day, delivered at 7am America/Los_Angeles")
    r.add_argument("--force", action="store_true", help="with --daily: send even if already sent today")
    r.add_argument("--preview", action="store_true",
                   help="email the full current digest (all matching roles from the last 14 days, "
                        "not just unsent ones) without changing what future daily emails include")

    sub.add_parser("test-email", help="send a short test email to confirm delivery is configured")

    ga = sub.add_parser("google-auth", help="one-time: authorize Gmail (read-only) + Sheets access")
    ga.add_argument("--client-secret", required=True, help="OAuth client JSON from Google Cloud console")
    tb = sub.add_parser("tracker-build", help="build data/Tracker.csv from exported recruiting emails (JSON)")
    tb.add_argument("messages", help="JSON list of {id, sender, subject, body|snippet, date (ISO) | date_ms}")
    tb.add_argument("--out", default="data/Tracker.csv")
    tr = sub.add_parser("tracker", help="sync your Google Sheet tracker with Gmail now")
    tr.add_argument("--dry-run", action="store_true", help="show what would change without writing")

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

    rp = sub.add_parser("resume-pdf", help="compact one-page resume PDF for email attachments")
    rps = rp.add_subparsers(dest="rp_cmd", required=True)
    x = rps.add_parser("parse", help="resume doc plain text -> JSON")
    x.add_argument("text"), x.add_argument("-o", "--out", required=True)
    x = rps.add_parser("render", help="JSON -> one-page PDF (+ .b64 next to it)")
    x.add_argument("json"), x.add_argument("-o", "--out", required=True)
    x.add_argument("--base", help="base resume JSON: refuse the render if tailoring invented facts")
    x = rps.add_parser("verify", help="check a draft's attached PDF matches the rendered file")
    x.add_argument("raw", help="file with get_draft RAW output (JSON or the raw string)")
    x.add_argument("pdf")

    aa = sub.add_parser("autoapply", help="prepare / submit applications to no-connection roles")
    aas = aa.add_subparsers(dest="aa_cmd", required=True)
    x = aas.add_parser("select", help="pick good-fit roles with no connection from reports/roles.json")
    x.add_argument("--roles", default="reports/roles.json"), x.add_argument("-o", "--out", required=True)
    x.add_argument("--exclude", help="file of role IDs already queued/declined (one per line)")
    x.add_argument("--max", type=int, default=15)
    x = aas.add_parser("scan", help="read each queued form and fill the standard answers")
    x.add_argument("queue"), x.add_argument("--facts", required=True)
    x = aas.add_parser("doc", help="queue + tailored resumes -> review Doc HTML")
    x.add_argument("queue"), x.add_argument("-o", "--out", required=True)
    x.add_argument("--resumes", required=True, help="dir with <slug>.json tailored resumes")
    x.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    x = aas.add_parser("doc-update", help="Docs batchUpdate requests that rewrite the review Doc in place")
    x.add_argument("queue"), x.add_argument("--resumes", required=True)
    x.add_argument("--read-doc", required=True, help="the Doc's read_doc JSON, saved to a file")
    x.add_argument("-o", "--out", required=True)
    x.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    x = aas.add_parser("kit", help="render every queued role's tailored resume PDF, named as in the Doc")
    x.add_argument("queue"), x.add_argument("--resumes", required=True), x.add_argument("--facts", required=True)
    x.add_argument("-o", "--out", required=True)
    x = aas.add_parser("submit", help="submit approved applications from the review Doc's text")
    x.add_argument("doc", help="the review Doc's text (Drive read_file_content), saved to a file")
    x.add_argument("--facts", required=True)
    x.add_argument("--base-dir", default="private", help="dir with base_ds.json / base_mle.json")
    x.add_argument("--only", default="", help="application numbers to submit, e.g. '1 3 5' (default: all READY)")
    x.add_argument("--out", default="private/apply/results.json")
    x.add_argument("--dry-run", action="store_true", help="fill the forms and screenshot them, don't submit")

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
            if already or local.hour < 1:
                print(f"daily: skipping ({'already sent today' if already else f'{local:%H:%M} PT is before 1am'})")
                return 0
        # Daily mode records what was alerted only after the email is delivered, so a failed
        # send doesn't silently drop roles from the next digest.
        tr = _tracker(cfg, write=not args.no_send)
        if tr is not None:
            print(f"tracker: {tr.events} recruiting emails, {len(tr.changes)} sheet updates, "
                  f"{len(tr.followups)} follow-ups")
        res = run(cfg, mark_seen=not args.daily, tracker=tr)
        md = render(res)
        cfg.reports_dir.mkdir(exist_ok=True)
        path = cfg.reports_dir / f"{datetime.now(timezone.utc):%Y-%m-%d-%H%M}.md"
        path.write_text(md)
        (cfg.reports_dir / "latest.md").write_text(md)
        (cfg.reports_dir / "latest-email.html").write_text(render_email(res)[1])
        # Scheduled runs happen overnight; Resend holds the email until 7am PT. A manual
        # (--force) run, or one that finishes after 7, sends right away.
        deliver_at = local.replace(hour=7, minute=0, second=0, microsecond=0)
        send_at = deliver_at if args.daily and not args.force and datetime.now(deliver_at.tzinfo) < deliver_at \
            else None
        sent = [] if args.no_send else deliver(res, md, always_email=args.daily, send_at=send_at)
        if args.daily and "email" in sent:
            from .pipeline import mark_delivered
            mark_delivered(cfg, res)
            _append_feed(cfg, res)
            _assist(cfg, res)
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

    if args.cmd == "google-auth":
        from .google import authorize
        creds = authorize(args.client_secret)
        print("\nAuthorized. Save these as GitHub repository secrets (Settings -> Secrets -> Actions):\n")
        for k, v in creds.items():
            print(f"  {k} = {v}")
        print("  TRACKER_SHEET_ID = <the long id in your sheet's URL>")
        return 0

    if args.cmd == "tracker-build":
        import json
        from .tracker import build_from_messages
        rows, changes = build_from_messages(json.loads(Path(args.messages).read_text()))
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rows)
        print(f"tracker-build: {rows.count(chr(10)) - 1} applications from {len(changes)} recruiting events "
              f"-> {args.out}")
        return 0

    if args.cmd == "tracker":
        tr = _tracker(cfg, write=not args.dry_run)
        if tr is None:
            print("tracker: set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN and TRACKER_SHEET_ID")
            return 1
        print(f"{tr.events} recruiting emails, {len(tr.changes)} sheet updates"
              + (" (dry run - nothing written)" if args.dry_run else ""))
        for c in tr.changes:
            print("  " + c)
        for f in tr.followups:
            print("  follow up: " + f)
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

    if args.cmd == "autoapply":
        from .autoapply_cli import main as aa_main
        return aa_main(args)

    if args.cmd == "resume-pdf":
        import base64
        import json
        from . import resume_pdf as rp
        if args.rp_cmd == "parse":
            r = rp.parse_text(Path(args.text).read_text())
            Path(args.out).write_text(json.dumps(r, indent=1, ensure_ascii=False))
            print(f"{args.out}: {len(r['experience'])} roles, "
                  f"{sum(len(x['bullets']) for x in r['experience'])} bullets")
            return 0
        if args.rp_cmd == "render":
            r = json.loads(Path(args.json).read_text())
            if args.base and (problems := rp.check(r, json.loads(Path(args.base).read_text()))):
                print("NOT RENDERED - fix these against the base resume:\n  " + "\n  ".join(problems))
                return 2
            info = rp.render(r, args.out)
            Path(args.out + ".b64").write_text(base64.b64encode(Path(args.out).read_bytes()).decode())
            print(json.dumps(info))
            return 0 if info["pages"] == 1 else 3
        ok, msg = rp.verify(Path(args.raw).read_text(), args.pdf)
        print(("OK: " if ok else "MISMATCH: ") + msg)
        return 0 if ok else 4

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


def _tracker(cfg, *, write: bool):
    """Sync the Google Sheet tracker with Gmail. None when not configured; failures are logged
    (by type only - Actions logs are public) and never block the digest."""
    from .google import Google, configured, sheet_id_from
    sheet = sheet_id_from(os.environ.get("TRACKER_SHEET_ID", ""))
    if not (configured() and sheet):
        return _tracker_snapshot(cfg)
    from .db import Store
    from .tracker import sync
    store = Store(cfg.db_path)
    try:
        return sync(Google(), sheet, cfg.profile.get("tracker") or {}, store, write=write)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("sourcer").error("tracker sync failed: %s", type(e).__name__)
        return None
    finally:
        store.close()


def _tracker_snapshot(cfg):
    """No Google access: read a CSV export of the tracker (data/Tracker.csv) so roles you've
    already applied to are skipped and its Connection column counts as warm contacts."""
    import csv
    path = Path(os.environ.get("SOURCER_TRACKER_CSV", cfg.root / "data" / "Tracker.csv"))
    if not path.exists():
        return None
    from .tracker import TrackerResult, TZ, followups, read_rows, warm_contacts
    values = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    _, rows, _ = read_rows(values)
    fu, todo = followups(rows, datetime.now(TZ).date())
    return TrackerResult(rows=rows, followups=fu, to_apply=todo, warm_contacts=warm_contacts(rows, []))


def _assist(cfg, res) -> None:
    """Calendar holds for events + Gmail drafts for outreach emails (needs Google access)."""
    from .google import Google, configured
    if not configured():
        return
    from .assist import draft_emails, hold_events
    from .db import Store
    opts = cfg.profile.get("assist") or {}
    g, store = Google(), Store(cfg.db_path)
    try:
        if opts.get("calendar_holds"):
            held, skipped = hold_events(g, res.events, store)
            print(f"calendar: {held} event holds added, {len(skipped)} skipped for conflicts")
        if opts.get("gmail_drafts"):
            print(f"gmail: {draft_emails(g, res, store)} outreach drafts created")
    except Exception as e:  # noqa: BLE001
        logging.getLogger("sourcer").error("calendar/gmail assist failed: %s", type(e).__name__)
    finally:
        store.close()


def _append_feed(cfg, res) -> None:
    from .google import Google, configured, sheet_id_from
    sheet = sheet_id_from(os.environ.get("TRACKER_SHEET_ID", ""))
    if not (configured() and sheet) or not (res.new_jobs or res.leads):
        return
    from .tracker import append_feed
    try:
        n = append_feed(Google(), sheet, cfg.profile.get("tracker") or {}, res)
        print(f"tracker: {n} rows added to the feed tab")
    except Exception as e:  # noqa: BLE001
        logging.getLogger("sourcer").error("tracker feed append failed: %s", type(e).__name__)


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
        tr = _tracker(cfg, write=False)
        res = run(cfg, mark_seen=False, tracker=tr)
        res.packets = []
        subject, html_body, text = render_email(res)
        subject = "[Preview] " + subject
        cfg.reports_dir.mkdir(exist_ok=True)
        (cfg.reports_dir / "preview-email.html").write_text(html_body)
        import json
        from .apply import roles_payload
        floor = (cfg.profile.get("autoapply") or {}).get("min_score", 30)
        pool = [sj for sj in res.all_scored if sj.score >= floor]
        if tr is not None:
            from .tracker import already_tracked
            pool = [sj for sj in pool if not already_tracked(sj.job.company, sj.job.title, tr.rows)]
        (cfg.reports_dir / "roles.json").write_text(json.dumps(roles_payload(pool), indent=1))
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
