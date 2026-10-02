from pathlib import Path

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


def test_headline_edge_cases_from_live_run():
    assert parse_headline("Brazil's Sharp raises $328,000 pre-seed to decode why salespeople win") == \
        ("Sharp", 328000.0, "Pre-Seed")
    assert parse_headline("Unveilr AI secures pre‑seed funding at Rs 16.7 cr valuation")[2] == "Pre-Seed"


def test_eeo_and_whitespace():
    from sourcer.models import Job
    from sourcer.sources.postings import Question
    assert Question("U.S. Equal Opportunity Employment Information (Completion is voluntary)").is_eeo
    assert not Question("How did you hear about this job?").is_eeo
    assert Job(company="X", title=" Account Manager ", url="", source="t", external_id="1").title == "Account Manager"


def test_form_d_enrich(fixture):
    ev = edgar.parse_search(fixture("edgar.json"))[0]
    xml = (Path(__file__).parent / "fixtures" / "form_d.xml").read_text()
    edgar.enrich(ev, xml)
    assert ev.amount_usd == 14e6 and "Other Technology" in ev.headline
    assert edgar.is_startup_raise(ev, xml)
    fund_xml = xml.replace("<offeringData>", "<offeringData><investmentFundInfo><investmentFundType>Hedge Fund"
                           "</investmentFundType></investmentFundInfo>")
    assert not edgar.is_startup_raise(ev, fund_xml)
    re_xml = xml.replace("Other Technology", "Other Real Estate")
    assert not edgar.is_startup_raise(ev, re_xml)


def test_real_headlines_from_live_run():
    cases = {
        "OuterSignal Raises $22M Series A to Expand AI-Powered Personalization Marketing - citybiz":
            ("OuterSignal", 22e6, "Series A"),
        "OuterSignal Raises $22 Million Series A To Expand Agentic Customer Personalization Platform":
            ("OuterSignal", 22e6, "Series A"),
        "Joe AI raises €2M to put AI agents inside real estate's inbox - app.dealroom.co": ("Joe AI", 2e6, None),
        "Exclusive: Homeward Raises $120M To Help Homeowners Buy And Sell More Quickly - Crunchbase News":
            ("Homeward", 120e6, None),
        "EliseAI raises $350M to enhance its AI work automation suite - siliconangle.com": ("EliseAI", 350e6, None),
        "Kanu AI raises $11.7m to turn how staff work into software they own": ("Kanu AI", 11.7e6, None),
        "Exclusive | AI Cyber Startup Armadin Raises $255 Million - WSJ": ("Armadin", 255e6, None),
        "Elio Mortgage Raises $5.1M to Build an AI-Native Mortgage Brokerage": ("Elio Mortgage", 5.1e6, None),
        "Home Interior Materials Startup Gravity Raises $15 Mn To Expand Into New Categories": ("Gravity", 15e6, None),
        "EDT raises $2.4 million in Pre-Series A, enters beauty-tech": ("EDT", 2.4e6, "Pre-Series A"),
    }
    for title, want in cases.items():
        got = parse_headline(title)
        assert got is not None, title
        assert (got[0], got[1], got[2]) == want, (title, got)
    for title in ("Top 50: Europe's most influential AI leaders - EU-Startups",
                  "Lobby, Crewfare win People's Choice Awards at the 2026 Global Startup Pitch",
                  "AI Real Estate Firms Keep Reeling in Millions of Dollars - therealdeal.com"):
        assert parse_headline(title) is None, title
