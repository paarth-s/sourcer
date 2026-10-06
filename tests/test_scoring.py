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


def test_location_filter(profile):
    ok, _ = score_job(job("Data Scientist", loc="San Francisco, CA"), profile, now=NOW)
    remote, _ = score_job(job("Data Scientist", loc="Remote - USA"), profile, now=NOW)
    assert ok >= profile["alert_threshold"] and remote == ok
    assert score_job(job("Data Scientist", loc="Seattle, WA"), profile, now=NOW)[0] == 0
    assert score_job(job("Data Scientist", loc="Bengaluru"), profile, now=NOW)[0] == 0
    assert score_job(job("Data Scientist", loc="New York, NY / San Francisco, CA"), profile, now=NOW)[0] == ok
    soft = {**profile, "location_strict": False}
    assert ok - score_job(job("Data Scientist", loc="Austin, TX"), soft, now=NOW)[0] == 15


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


def test_broad_titles_need_similar_work(profile):
    analytic = ("Own experimentation and A/B testing for the rentals funnel. Build forecasting and "
                "regression models in SQL and Python; define KPIs.")
    dashboards = "Build and maintain Tableau dashboards and monthly reports for leadership."
    bi, why = score_job(job("Senior BI Analyst", analytic), profile, now=NOW)
    assert bi >= profile["alert_threshold"] and any(r.startswith("similar work") for r in why)
    assert score_job(job("BI Analyst", dashboards), profile, now=NOW)[0] == 0
    assert score_job(job("Data Analyst, Pricing", analytic), profile, now=NOW)[0] >= profile["alert_threshold"]
    # Strong titles are not subject to the work check
    assert score_job(job("Data Scientist", dashboards), profile, now=NOW)[0] >= profile["alert_threshold"]
    assert score_job(job("Sales Analyst", analytic), profile, now=NOW)[0] == 0  # excluded family


def test_broad_titles_skip_finance_and_eng(profile):
    analytic = "SQL, Python, forecasting, regression, KPIs, experimentation and A/B testing."
    assert score_job(job("Strategic Financial Analyst", analytic), profile, now=NOW)[0] == 0
    assert score_job(job("Principal, Strategic Finance & Analytics", analytic), profile, now=NOW)[0] == 0
    assert score_job(job("Staff Engineer - Experimentation Platform", analytic), profile, now=NOW)[0] == 0
    assert score_job(job("Lead Advanced Analytics, Product", analytic), profile, now=NOW)[0] >= 45
