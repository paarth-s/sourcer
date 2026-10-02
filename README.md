# sourcer

Finds data science / ML roles that fit your background **as they go live, or before
they exist**, and points you to the people to contact.

It runs every 3 hours on GitHub Actions and sends you a digest with two sections:

1. **New roles, apply early.** Matching postings pulled straight from company job
   boards (Greenhouse, Lever, Ashby, Workable) within hours of publication. Job
   aggregators and LinkedIn usually pick these up days later. Each role shows the
   connections you have there, so you can ask for a referral.
2. **Reach out before the role exists.** Recently funded companies that fit your
   profile but have no matching role open yet. Each one comes with the funding news,
   the people you know there, LinkedIn search links for 2nd-degree contacts, and
   (optionally) an outreach note drafted by Claude. If a company is hiring data
   engineers but not data scientists yet, its score goes up: a DS opening usually
   follows.

## Where the signals come from

| Signal | Source | Why it's early |
|---|---|---|
| Funding announcements | Google News RSS searches built around your verticals and ML areas, plus TechCrunch, Crunchbase News, FinSMEs and PR Newswire | Companies usually hire 0–3 months after a raise |
| Stealth raises | SEC Form D filings (EDGAR full-text search) | Filed within 15 days of the first sale, often **before** the press release |
| Job postings | Public Greenhouse / Lever / Ashby / Workable APIs | Postings appear here first, the moment they go live |
| Startup roles | HN "Who is hiring?" (monthly) | Founders post directly |
| Warm paths | Your LinkedIn connections export | Gives you a referral path at every company where you know someone |

**Which companies get watched:** your `watchlist.yaml`, every recently funded company
that fits your profile, and **every company where you have a LinkedIn connection**.
sourcer finds each company's job board automatically and polls it on every run, so
you hear about a relevant role at a company where you know someone even if that
company didn't just raise.

**Scoring** (`profile.yaml`, all editable): title match (DS / applied ML / product DS)
plus domain matches in the description (pricing, recommendations, personalization,
airline, real estate, …), a recent-funding bonus, a connection bonus, freshness, and
your location preferences. If `ANTHROPIC_API_KEY` is set, Claude re-ranks the top 15
against your summary and drafts outreach notes for the top 5 leads.

## Setup (about 15 minutes)

1. **Fill in `profile.yaml`.** The `summary` matters most if you use the Claude
   step: add years of experience, companies, and notable projects. Adjust
   `locations` and `alert_threshold` too.
2. **Export your LinkedIn connections.** On LinkedIn, go to Settings & Privacy →
   Data privacy → *Get a copy of your data* → select only **Connections** → Request
   archive. The email with `Connections.csv` arrives in about 10 minutes. Then:
   ```bash
   base64 -i Connections.csv | gh secret set LINKEDIN_CONNECTIONS_B64
   ```
   Re-export monthly. **Never commit this file.** (`data/*.csv` is gitignored.)
3. **Choose how alerts reach you.** Add any of these as repo secrets
   (Settings → Secrets and variables → Actions):
   - Email: `SMTP_HOST` (e.g. `smtp.gmail.com`), `SMTP_PORT` (587), `SMTP_USER`,
     `SMTP_PASSWORD` (for Gmail, an [app password](https://myaccount.google.com/apppasswords)),
     `DIGEST_EMAIL_TO`
   - Phone push: `NTFY_TOPIC`. Pick a long random topic name and subscribe to it in the [ntfy](https://ntfy.sh) app
   - Slack: `SLACK_WEBHOOK_URL`
4. **Required for SEC data:** `SEC_USER_AGENT`, e.g. `Your Name you@example.com`.
   SEC's fair-access policy requires a contact string.
5. **Optional, for Claude re-ranking and drafts:** `ANTHROPIC_API_KEY`. Only the top
   candidates are sent, so each run costs cents.
6. Actions tab → *sourcer* → **Run workflow** to start the first run. The first run
   discovers boards and records a baseline. It only alerts on postings from the last
   14 days, so you don't get flooded.

### Run locally

```bash
pip install -e ".[llm,dev]"
cp ~/Downloads/Connections.csv data/
python -m sourcer network               # where your network is concentrated
python -m sourcer probe "Some Startup"  # find a company's job board
python -m sourcer run --no-send -v      # full run; report in reports/latest.md
pytest
```

## Privacy

This repo is public. Your connections file is a secret and is never written to the
repo or to logs. Run output goes to a file inside the runner rather than the public
Actions log, and the state DB lives in the Actions cache. **Making the repo private
is still recommended** (Settings → General → Change visibility). The scheduled run
uses about 2–4 minutes, well inside the free private-repo Actions quota.

## Limitations and next steps

- **2nd-degree connections:** LinkedIn's export only includes 1st-degree connections,
  and scraping LinkedIn breaks its terms and gets accounts restricted. For mutual
  connections, use the 2nd-degree search links in each lead, or add people to
  `warm_contacts` in `profile.yaml`.
- **Board discovery** guesses slugs from the company name. If a company uses
  Workday, its own careers site, or an unusual slug, add `ats` + `slug` to
  `watchlist.yaml` by hand.
- **Headline parsing** is regex-based and misses some phrasings. Each company only
  needs one outlet's phrasing to match.
- Possible additions: YC's company directory, Wellfound, and an investor-portfolio
  watch (new companies added to a16z, Sequoia or other funds' portfolio pages).
