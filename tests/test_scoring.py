from datetime import datetime, timezone

from sourcer.models import FundingEvent, Job
from sourcer.scoring import company_fit, score_job

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


def job(title, desc="", loc="Remote", posted=NOW):
    return Job(company="Co", title=title, url="u", source="greenhouse", external_id=title,
               location=loc, description=desc, posted_at=posted)


def test_strong_fit_beats_generic(profile):
    strong, why = score_job(job("Senior Data Scientist, Pricing",
                                "dynamic pricing for airline revenue management"), profile, now=NOW)
    generic, _ = score_job(job("Data Scientist", "dashboards"), profile, now=NOW)
    assert strong > generic >= profile["alert_threshold"]
    assert any("airline" in r for r in why)


def test_excluded_titles(profile):
    assert score_job(job("Data Science Intern"), profile, now=NOW)[0] == 0
    assert score_job(job("Account Executive"), profile, now=NOW)[0] == 0


def test_connections_and_funding_boost(profile):
    base, _ = score_job(job("Product Analyst"), profile, now=NOW)
    ev = FundingEvent(company="Co", headline="Co raises $10M Series A", url="", source="t", round="Series A")
    boosted, why = score_job(job("Product Analyst"), profile, n_connections=2, funding=ev, now=NOW)
    assert boosted >= base + 30
    assert "2 connections there" in why


def test_location_penalty(profile):
    ok, _ = score_job(job("Data Scientist", loc="New York, NY"), profile, now=NOW)
    far, _ = score_job(job("Data Scientist", loc="Austin, TX"), profile, now=NOW)
    assert ok - far == 15
    assert score_job(job("Data Scientist", loc="Bengaluru"), profile, now=NOW)[0] == 0


def test_company_fit(profile):
    ev = FundingEvent(company="Fareloop", headline="Fareloop raises $14M Series A",
                      summary="AI dynamic pricing for airlines", url="", source="t",
                      round="Series A", amount_usd=14e6)
    score, why = company_fit(ev, profile)
    assert score >= 30
    other = FundingEvent(company="Crateful", headline="Crateful raises $40M", summary="warehouses",
                         url="", source="t", round="Series B", amount_usd=40e6)
    assert company_fit(other, profile)[0] < 18


def test_foreign_remote_penalized(profile):
    us, _ = score_job(job("Senior Data Scientist", loc="Remote - USA"), profile, now=NOW)
    emea, _ = score_job(job("Senior ML Engineer, Voice Agents - EMEA Remote", loc="Paris, France (Remote)"),
                        profile, now=NOW)
    pl, _ = score_job(job("Senior Data Scientist", loc="Remote (Poland) or Cracow"), profile, now=NOW)
    assert us >= profile["alert_threshold"] and emea == 0 and pl == 0
    assert score_job(job("Director, Data Science"), profile, now=NOW)[0] == 0
    p, why = score_job(job("Principal Data Scientist"), profile, now=NOW)
    assert "stretch level" in why


def test_company_fit_without_named_round(profile):
    ev = FundingEvent(company="Joe AI", headline="Joe AI raises €2M to put AI agents inside real estate's inbox",
                      url="", source="t", amount_usd=2e6)
    assert company_fit(ev, profile)[0] >= 18
    late = FundingEvent(company="Big", headline="Big raises $350M for real estate automation", url="",
                        source="t", amount_usd=350e6)
    assert company_fit(late, profile)[0] < 18
