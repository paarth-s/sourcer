from __future__ import annotations

import argparse
import logging
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

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.root)

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
    return 1


if __name__ == "__main__":
    sys.exit(main())
