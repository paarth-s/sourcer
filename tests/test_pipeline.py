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
