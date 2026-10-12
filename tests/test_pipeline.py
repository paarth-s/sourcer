import re
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from sourcer import pipeline
from sourcer.config import load_config
from sourcer.digest import render
from sourcer.sources import ats
from sourcer.sources.funding import parse_feed

ROOT = Path(__file__).parent.parent


def _setup(tmp_path, fixture, monkeypatch):
    for f in ("profile.yaml", "sources.yaml"):
        shutil.copy(ROOT / f, tmp_path / f)
    (tmp_path / "watchlist.yaml").write_text("companies: []\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "Connections.csv").write_text(fixture("Connections.csv"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    boards = {("greenhouse", "fareloop"): fixture("greenhouse.json"),
              ("lever", "nestwise"): fixture("lever.json")}
    calls = []

    def fake_fetch_board(s, provider, slug, company):
        calls.append((provider, slug))
        data = boards.get((provider, slug))
        if data is None:
            return None
        return ats.PROVIDERS[provider][1](data, company)

    monkeypatch.setattr(ats, "fetch_board", fake_fetch_board)
    # Pin news dates to "now" so lead freshness windows don't make this test rot.
    feed = re.sub(r"<pubDate>.*?</pubDate>",
                  f"<pubDate>{format_datetime(datetime.now(timezone.utc))}</pubDate>",
                  fixture("google_news.xml"))
    monkeypatch.setattr(pipeline, "fetch_funding", lambda cfg: parse_feed(feed, "t"))
    monkeypatch.setattr(pipeline, "fetch_form_d", lambda cfg, names: [])
    monkeypatch.setattr(pipeline, "fetch_hn_jobs", lambda cfg: [])
    monkeypatch.setattr(pipeline, "fetch_yc", lambda profile: [])
    monkeypatch.setattr("sourcer.sources.events.fetch_events", lambda s, profile: [])
    return load_config(tmp_path), calls


def test_end_to_end(tmp_path, fixture, monkeypatch):
    cfg, calls = _setup(tmp_path, fixture, monkeypatch)
    res = pipeline.run(cfg)

    # Fareloop: funded + on-profile + has a pricing DS role + you know someone there.
    assert [sj.job.title for sj in res.new_jobs] == ["Senior Data Scientist, Pricing"]
    top = res.new_jobs[0]
    assert top.connections[0].name == "Sam Lee" and top.funding is not None

    # Nestwise: funded proptech, no DS role yet but hiring a Data Engineer -> outreach lead.
    leads = {l.company: l for l in res.leads}
    assert "Nestwise" in leads and "Fareloop" not in leads and "Crateful" not in leads
    assert leads["Nestwise"].adjacent_roles[0].title == "Data Engineer"
    assert leads["Nestwise"].connections[0].name == "Priya Shah"

    md = render(res)
    assert "Senior Data Scientist, Pricing" in md and "Reach out before the role exists" in md

    # Second run: nothing is new, boards come from the DB without re-probing.
    calls.clear()
    res2 = pipeline.run(cfg)
    assert res2.new_jobs == [] and res2.leads == []
    assert set(calls) == {("greenhouse", "fareloop"), ("lever", "nestwise")}


def test_unsent_roles_are_not_marked(tmp_path, fixture, monkeypatch):
    cfg, _ = _setup(tmp_path, fixture, monkeypatch)
    res = pipeline.run(cfg, mark_seen=False)       # email failed: nothing recorded
    assert res.new_jobs
    res2 = pipeline.run(cfg, mark_seen=False)
    assert [j.job.uid for j in res2.new_jobs] == [j.job.uid for j in res.new_jobs]
    pipeline.mark_delivered(cfg, res2)             # email sent
    assert pipeline.run(cfg).new_jobs == []


def test_resend_schedules_overnight_digest(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from sourcer import notify
    sent = []

    class Ok:
        ok = True
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("DIGEST_EMAIL_TO", "me@example.com")
    monkeypatch.setattr(notify.requests, "post", lambda url, json, **kw: sent.append(json) or Ok())
    at = datetime(2026, 10, 13, 7, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert notify.send_resend("s", "<p>h</p>", "t", send_at=at)
    assert sent[-1]["scheduled_at"] == "2026-10-13T07:00:00-07:00"
    assert notify.send_resend("s", "<p>h</p>", "t")
    assert "scheduled_at" not in sent[-1]


def test_tracker_filters_and_feeds_digest(tmp_path, fixture, monkeypatch):
    from sourcer.email_html import render_email
    from sourcer.tracker import TrackerResult, read_rows
    cfg, _ = _setup(tmp_path, fixture, monkeypatch)
    _, rows, _ = read_rows([["Job Title", "Company", "Current Status", "Connection"],
                            ["Sr Data Scientist, Pricing", "Fareloop", "Applied", ""],
                            ["DS", "Nestwise", "To Apply", "Kim"]])
    tr = TrackerResult(rows=rows, changes=["DS @ Acme: Applied -> Rejected (10/1/2026)"],
                       followups=["MLE @ Beta: send a check-in"], to_apply=["DS @ Nestwise (via Kim)"],
                       warm_contacts={"Nestwise": [{"name": "Kim", "position": "connection from your tracker"}]})
    res = pipeline.run(cfg, mark_seen=False, tracker=tr)
    assert res.new_jobs == [] and res.skipped_tracked == 1          # already in your sheet
    lead = next(l for l in res.leads if l.company == "Nestwise")
    assert lead.connections[0].name == "Kim"                         # tracker contact ranks first
    _, html, text = render_email(res)
    assert "Your tracker" in html and "send a check-in" in html and "Applied -> Rejected" in text
