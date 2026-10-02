from sourcer.linkedin import Network, parse_connections
from sourcer.models import normalize_company
from sourcer.sources import ats, edgar, hn
from sourcer.sources.funding import parse_feed, parse_headline


def test_parse_headline_variants():
    assert parse_headline("Fareloop raises $14M Series A to bring AI pricing - TechCrunch") == \
        ("Fareloop", 14e6, "Series A")
    assert parse_headline("Proptech startup Nestwise lands $6.5 million seed round") == \
        ("Nestwise", 6.5e6, "Seed")
    co, amt, rnd = parse_headline("Crateful, the B2B logistics platform, secures $40M in Series B funding")
    assert (co, amt, rnd) == ("Crateful", 40e6, "Series B")
    assert parse_headline("Why seed rounds are getting bigger") is None


def test_parse_feed(fixture):
    events = parse_feed(fixture("google_news.xml"), "test")
    assert [e.company for e in events] == ["Fareloop", "Nestwise", "Crateful"]
    assert "revenue management" in events[0].summary
    assert events[0].published.year == 2026


def test_ats_parsers(fixture):
    gh = ats.parse_greenhouse(fixture("greenhouse.json"), "Fareloop")
    assert gh[0].title == "Senior Data Scientist, Pricing"
    assert "dynamic pricing" in gh[0].description and "<p>" not in gh[0].description
    assert gh[0].location == "Remote - US"

    lv = ats.parse_lever(fixture("lever.json"), "Nestwise")
    assert lv[0].posted_at.year == 2025 and "Own pipelines" in lv[0].description

    ab = ats.parse_ashby(fixture("ashby.json"), "X")
    assert "Remote" in ab[0].location

    wk = ats.parse_workable(fixture("workable.json"), "Acme")
    assert wk[0].location == "Chicago, IL, United States" and wk[0].uid == "workable:W1"


def test_slug_candidates():
    c = ats.slug_candidates("Acme Travel, Inc.")
    assert c[:3] == ["acmetravelinc", "acme-travel-inc", "acmetravel"]
    assert "acme-travel" in c


def test_edgar_filters_funds(fixture):
    evs = edgar.parse_search(fixture("edgar.json"))
    assert len(evs) == 1
    assert evs[0].company == "Fareloop Inc" and evs[0].key == "fareloop"
    assert evs[0].url.endswith("/1999999/000199999926000001/primary_doc.xml")


def test_hn_thread(fixture):
    jobs = hn.parse_thread(fixture("hn_thread.json"))
    assert len(jobs) == 1
    assert jobs[0].company == "Stayline" and "Remote" in jobs[0].location


def test_connections(fixture):
    conns = parse_connections(fixture("Connections.csv"))
    assert len(conns) == 2  # row without company dropped
    net = Network(conns, {"Stayline": ["Jo (ex-coworker)"]})
    assert net.at("Nestwise")[0].name == "Priya Shah"
    assert net.at("stayline")[0].position == "warm contact"


def test_normalize_company():
    assert normalize_company("Nestwise, Inc.") == normalize_company("nestwise") == "nestwise"
    assert normalize_company("Booking.com") == "booking com"
