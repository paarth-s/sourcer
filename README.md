# sourcer

Finds data science / ML roles that fit your background **as they go live, or before
they exist**, and points you to the people to contact.

Every morning at about 7 AM Pacific, a GitHub Actions job scans the sources below and
emails you a digest with three parts:

1. **New roles, apply early.** Matching postings pulled straight from company job
   boards (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable), usually
   within a day of publication. Job
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

Each role and lead also gets a **"Who to contact"** section: 1–3 key people (hiring
manager, recruiter, founders for small startups) and the exact message to send. For
LinkedIn, that's a connection note under 300 characters plus a follow-up for once
they accept; for 1st-degree connections, the message goes directly. If someone's email
is published on a public page, you get a drafted email instead. Guardrails:
- A LinkedIn URL is shown only if it appeared in real search results; otherwise you
  get a search link marked "profile not confirmed".
- An email is shown only if it appears on a fetched public page. Addresses are never
  guessed.

This needs `ANTHROPIC_API_KEY`. Optional private files (git-ignored):
- `private/outreach_examples.md`: messages you've written, so drafts match your tone.
- `private/outreach_log.yaml`: people you've already contacted, so they're skipped:
  ```yaml
  contacted:
    - {name: Jane Doe, company: Zillow, date: 2026-10-06}
  ```

## Where the signals come from

| Signal | Source | Why it's early |
|---|---|---|
| Funding announcements | Google News RSS searches built around your verticals and ML areas, plus TechCrunch, Crunchbase News, FinSMEs and PR Newswire | Companies usually hire 0–3 months after a raise |
| Job postings | Public Greenhouse / Lever / Ashby / SmartRecruiters / Workable APIs, and Workday search for large companies (Zillow, Expedia, Redfin, Priceline...) | Postings appear here first, the moment they go live |
| Hiring startups | Y Combinator's public list of hiring companies, filtered by your verticals and ML areas | Startups that never make the funding news |
| Stealth raises (optional) | SEC Form D filings, opt-in via `SEC_USER_AGENT` | Filed within 15 days of a round, often **before** the press release |
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
3. **Set up the daily email** (about 3 minutes). Add these repo secrets under
   Settings → Secrets and variables → Actions:
   - `DIGEST_EMAIL_TO`: the address the digest goes to.
   - `RESEND_API_KEY`: sign up at [resend.com](https://resend.com) **using that same
     address**, then go to API Keys → Create. On the free tier, Resend can send to its
     account owner without any domain setup.

   Alternatively, use SMTP: set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER` and
   `SMTP_PASSWORD`, e.g. a Gmail account with an
   [app password](https://myaccount.google.com/apppasswords).

   To check delivery, go to Actions → *sourcer* → Run workflow → mode `test-email`.
   - Optional extras: `NTFY_TOPIC` for phone push (pick a long random topic and
     subscribe in the [ntfy](https://ntfy.sh) app), `SLACK_WEBHOOK_URL`.
4. **Optional SEC data:** `SEC_USER_AGENT`, e.g. `Your Name you@example.com`. SEC
   requires a contact email to read filings.
5. **For Claude re-ranking, outreach drafts and application packets:**
   `ANTHROPIC_API_KEY`. Only the top candidates are sent, so each run costs cents,
   plus roughly $0.10–0.30 per packet. For packets in scheduled runs, also add
   `RESUME_YAML_B64` and `ANSWERS_YAML_B64`:
   `base64 -i private/resume.yaml | gh secret set RESUME_YAML_B64`, and the same
   for answers.
6. To send a digest now, go to Actions → *sourcer* → **Run workflow** (mode `daily`).
   The first run discovers job boards and records a baseline. It only includes
   postings from the last 14 days, so you don't get flooded. After that, the email
   arrives on its own every morning. On quiet days it says "nothing new" so you know
   it ran. If delivery breaks, the run fails and GitHub emails you about the failure.

### Run locally

```bash
pip install -e ".[llm,dev]"
cp ~/Downloads/Connections.csv data/
python -m sourcer network               # where your network is concentrated
python -m sourcer probe "Some Startup"  # find a company's job board
python -m sourcer run --no-send -v      # full run; reports/latest.md + reports/latest-email.html
python -m sourcer test-email            # check email delivery
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
- **Board discovery** guesses slugs from the company name. For Workday companies, add
  the careers URL (`workday: https://<co>.wd5.myworkdayjobs.com/<site>`) to
  `watchlist.yaml`. Companies on fully custom career sites (United, FLYR, Fetcherr)
  can't be scanned and are listed in the watchlist as ones to check by hand.
- **Headline parsing** is regex-based and misses some phrasings. Each company only
  needs one outlet's phrasing to match.
- Possible additions: Wellfound, and an investor-portfolio watch (new companies added
  to a16z, Sequoia or other funds' portfolio pages).
