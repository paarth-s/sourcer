# sourcer

Finds data science / ML roles that fit your background **as they go live, or before
they exist**, and points you to the people to contact.

It runs every 3 hours on GitHub Actions and sends you a digest with three parts:

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

3. **An application packet for your best new roles.** For each one you get a resume
   tailored to that posting (a one-page PDF) and drafted answers to its application
   questions. Packets are attached to the digest email. See
   [Resume tailoring and application answers](#resume-tailoring-and-application-answers).

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
5. **For Claude re-ranking, outreach drafts and application packets:**
   `ANTHROPIC_API_KEY`. Only the top candidates are sent, so each run costs cents,
   plus roughly $0.10–0.30 per packet. For packets in scheduled runs, also add
   `RESUME_YAML_B64` and `ANSWERS_YAML_B64`:
   `base64 -i private/resume.yaml | gh secret set RESUME_YAML_B64`, and the same
   for answers.
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

## Resume tailoring and application answers

Your resume lives in `private/resume.yaml` (git-ignored). It's a bank of every bullet
from your DS, MLE and Product Analytics resumes, including the per-variant phrasings,
titles and bullet order. To prepare a packet for any posting:

```bash
python -m sourcer apply https://job-boards.greenhouse.io/acme/jobs/123
python -m sourcer apply https://jobs.lever.co/acme/<id> --questions questions.txt
python -m sourcer apply --jd posting.txt --company Acme --title "Product Data Scientist"
python -m sourcer resume --variant mle     # render a base version, untailored
```

Each packet goes in `applications/<date>-<company>-<role>/` and contains
`resume.pdf`, `resume.html`, `application.md` (what changed, gaps, every answer with a
status), `answers.json` and `posting.txt`.

How it works:
1. **Pick a base resume.** The DS, MLE or Product version is chosen from the posting's
   title and description. `--variant` overrides it.
2. **Tailor.** Claude reorders bullets by relevance, rephrases them to use the
   posting's vocabulary, rewrites the objective, reorders skills, and picks the best
   of your job titles. It also lists *gaps*: requirements your resume doesn't show.
3. **Check every change** before rendering:
   - Each bullet must cite its source bullet. Any number that isn't in the source
     (a new metric, a changed %) reverts the bullet to your original wording.
   - Bullets that grow more than 20% revert too.
   - Skills and titles can only come from your bank, so tools you don't list can't
     be added.

   Every reverted bullet is listed under "Check these".
4. **Answer the questions.** Greenhouse publishes each job's application form, so its
   questions are fetched automatically. For Lever, Ashby, Workable or anything else,
   put the questions in a text file, separated by blank lines, and pass `--questions`.
   - Name, email, phone and LinkedIn are filled in from your bank.
   - Claude drafts the rest using only your resume and `private/answers.yaml`.
   - Anything that needs a fact you haven't provided (sponsorship, salary, start date,
     relocation) is marked **NEEDS INPUT** rather than guessed.
   - Demographic, EEO, veteran and disability questions are never answered or sent
     to Claude.

**Fill in `private/answers.yaml` once.** It holds work authorization, salary range,
start date, and a few short stories in your own words for "why us" or "tell us about a
project" questions.

Only your name and resume content go to Claude. Your contact details don't.

## Privacy

This repo is public. Your connections file, resume bank and answers are secrets.
They're never written to the repo or to logs. Application packets are only emailed
to you. Run output goes to a file inside the runner rather than the public
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
