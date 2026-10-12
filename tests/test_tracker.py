from datetime import date, datetime, timezone

from sourcer import tracker as T

HDR = ["Job Title", "Company", "Location", "Pay", "Application Date", "Current Status",
       "Last Contact Date", "Recruiter Info", "Connection"]


def msg(sender, subject, body, day=10, mid=None):
    ms = int(datetime(2026, 9, day, 18, tzinfo=timezone.utc).timestamp() * 1000)
    return {"id": mid or f"{sender}{subject}{day}", "sender": sender, "subject": subject, "body": body, "date_ms": ms}


def test_classify_real_world_phrasings():
    c = T.classify
    assert c(msg("no-reply@ashbyhq.com", "Thank you for applying to Zentra",
                 "Hi P, Thanks for applying to Zentra! We've received your application for the Founding Data "
                 "Scientist role.")).kind == "applied"
    rej = c(msg("noreply@orbix.com", "Thank you for your application to Orbix",
                "Thank you for your interest in the position of Principal Applied Scientist - Robotics-341749. "
                "The hiring manager has chosen to move forward with another candidate."))
    assert rej.kind == "rejected" and rej.role == "Principal Applied Scientist - Robotics"
    assert c(msg("no-reply@wingo.com", "Thank you for your interest in Senior Data Scientist at Wingo",
                 "While your background is impressive, we have chosen to move forward with other candidates "
                 "for this specific role.")).kind == "rejected"
    assert c(msg("stord@myworkday.com", "Important Update: Lead Data Scientist at Stordo",
                 "the requisition for the Lead Data Scientist position has been closed/cancelled")).kind == "closed"
    assert c(msg("scheduling@recruiting.acmebank.com", "Interview confirmation: P S",
                 "Great news! Your interview time is confirmed.")).kind == "interview"
    ref = c(msg("no-reply@ashbyhq.com", "You've Been Referred for the Member of Data Science Staff Role at Zentra",
                "Jordan Lee has shared your information with us and referred you for consideration"))
    assert ref.kind == "referral" and ref.referrer == "Jordan Lee"
    # an application confirmation that mentions "next steps" is not an interview
    assert c(msg("no-reply@ashbyhq.com", "Thanks for applying to Mercato!",
                 "We will review your application and get back to you if there are next steps.")).kind == "applied"
    assert c(msg("jobnotification@delta.avature.net", "Job alert for new Delta opportunities",
                 "Thank you for your interest in a career with Delta!")) is None
    # a person asking for availability is an interview signal
    assert c(msg("Pat Doe <pat@quantra.com>", "Quantra - Data Science Opportunity",
                 "Your experience is impressive - would love to set up a call. Please share your availability."
                 )).kind == "interview"


def test_company_detection():
    known = {"pge": "PGE", "doordash": "Doordash", "wing": "Wing", "generalmotors": "GM", "salesforce": "Salesforce"}
    ev = T.classify(msg("SystemMessage@successfactors.com", "Thank you for your interest in PG&E",
                        "we will not be moving forward with your application"))
    assert T.find_company(ev, {"subject": "Thank you for your interest in PG&E", "body": ""}, known) == "pge"
    m = msg("no-reply@us.greenhouse-mail.io", "DoorDash Application Status", "we have decided to not move forward "
            "with your application")
    assert T.find_company(T.classify(m), m, known) == "doordash"
    m = msg("generalmotors@myworkday.com", "Your interest in Senior Data Scientist at GM", "moved forward with other "
            "candidates")
    assert T.find_company(T.classify(m), m, known) == "generalmotors"
    # lowercase english word doesn't count as the company
    m = msg("no-reply@ashbyhq.com", "Thanks for applying to Nubank!", "We're on the wing. Thanks for applying to Nubank")
    assert T.find_company(T.classify(m), m, known) == "nubank" and known["nubank"] == "Nubank"


def rows(*data):
    _, out, _ = T.read_rows([HDR] + [list(r) for r in data])
    return out


def test_apply_events_updates_and_guards():
    rs = rows(["Sr DS", "Salesforce", "SF", "", "", "To Apply", "", "", ""],
              ["Sr MLE", "Salesforce", "SF", "", "", "To Apply", "", "", ""],
              ["4 roles (1 DS / 3 MLE)", "Indeed", "Remote", "", "", "Second Round", "", "", "Max"],
              ["Senior Applied Scientist", "Oracle", "Remote", "", "", "Applied", "", "", "Sam"],
              ["DS", "Clay", "SF", "", "", "Applied", "", "", ""],
              ["DS", "US Bank", "SF", "", "", "To Apply", "", "", "Drew"])
    ev = lambda kind, comp, role, day: (T.Event(kind, comp, role, datetime(2026, 10, day, 18, tzinfo=timezone.utc),
                                                "x@y.com", f"{kind}{role}{day}"), T.company_key(comp))
    events = [ev("applied", "salesforce", "Senior Data Scientist - GTM", 1),
              ev("applied", "salesforce", "Lead Data Scientist", 1),
              ev("rejected", "salesforce", "Senior Data Scientist - GTM", 7),
              ev("rejected", "indeed", "Machine Learning Engineer II", 7),
              ev("rejected", "oracle", "Principal Machine Learning Engineer", 6),
              ev("rejected", "clay", "Data Scientist", 2),
              ev("interview", "usbank", "", 9),
              ev("applied", "nubank", "Data Scientist", 9)]
    display = {T.company_key(r.get("company")): r.get("company") for r in rs}
    display["nubank"] = "Nubank"
    edits, new, changes = T.apply_events(rs, events, display=display)
    by = {(r.get("company"), r.get("title")): r for r in rs}
    assert by[("Salesforce", "Sr DS")].get("status") == "Rejected"
    assert by[("Salesforce", "Sr DS")].get("applied_on") == "10/1/2026"
    assert by[("Salesforce", "Sr MLE")].get("status") == "To Apply"          # untouched
    assert by[("Indeed", "4 roles (1 DS / 3 MLE)")].get("status") == "Second Round"   # umbrella row: note only
    assert "rejected" in by[("Indeed", "4 roles (1 DS / 3 MLE)")].get("notes")
    assert by[("Oracle", "Senior Applied Scientist")].get("status") == "Applied"  # different Oracle role
    assert by[("Clay", "DS")].get("status") == "Rejected"
    assert by[("US Bank", "DS")].get("status") == "First Round"
    titles = {(c["company"], c["title"], c["status"]) for c in new}
    assert ("Salesforce", "Lead Data Scientist", "Applied") in titles
    assert ("Nubank", "Data Scientist", "Applied") in titles
    assert all(n > 0 for n in edits)


def test_followups_and_warm_contacts():
    rs = rows(["DS", "Acme", "", "", "9/1/2026", "Applied", "9/1/2026", "", "Remy"],
              ["MLE", "Beta", "", "", "", "First Round", "9/20/2026", "jane@beta.com", ""],
              ["DS", "Gamma", "", "", "", "To Apply", "", "", "Avery"],
              ["DS", "Delta", "", "", "", "Applied", "", "", "UMich alum"])
    fu, todo = T.followups(rs, date(2026, 9, 30))
    assert any("Remy" in f for f in fu) and any("jane@beta.com" in f for f in fu)
    assert todo == ["DS @ Gamma (via Avery)"]
    warm = T.warm_contacts(rs, [["Person", "Company"], ["Lou", "JPMC"], ["Ira", ""]])
    assert warm["Acme"][0]["name"] == "Remy" and "Delta" not in warm
    assert warm["jpmorgan chase"][0]["name"] == "Lou"
    assert T.already_tracked("Acme", "Data Scientist", rs)
    assert not T.already_tracked("Acme", "Staff Machine Learning Engineer", rs)


class FakeGoogle:
    def __init__(self, values, messages):
        self.values, self.messages, self.updates, self.appends = values, messages, [], []

    def sheet_values(self, sid, rng):
        return self.values if "Main" in rng else [["Person", "Company"], ["Rae", "Acme"]]

    def sheet_update(self, sid, upd):
        self.updates += upd

    def sheet_append(self, sid, rng, rows):
        self.appends += rows

    def gmail_search(self, q, limit):
        return [m["id"] for m in self.messages]

    def gmail_message(self, mid):
        return next(m for m in self.messages if m["id"] == mid)


class FakeStore:
    def __init__(self):
        self.meta = {}

    def get_meta(self, k):
        return self.meta.get(k)

    def set_meta(self, k, v):
        self.meta[k] = v


def test_sync_end_to_end():
    values = [HDR, ["DS", "Acme", "SF", "", "", "Applied", "", "", "Remy"]]
    msgs = [msg("no-reply@acme.com", "Update from Acme", "we will not be moving forward with your application "
                "for the Data Scientist role", day=20, mid="m1"),
            msg("no-reply@ashbyhq.com", "Thanks for applying to Zentra!", "Thanks for applying to Zentra! We "
                "received your application for the Machine Learning Engineer role.", day=21, mid="m2")]
    g, store = FakeGoogle(values, msgs), FakeStore()
    res = T.sync(g, "sheet", {"tab": "Main Arena", "people_tab": "Sheet2"}, store,
                 now=datetime(2026, 9, 22, tzinfo=timezone.utc))
    cells = dict((rng, v[0][0]) for rng, v in g.updates)
    assert cells["'Main Arena'!J1"] == "Auto notes"
    assert cells["'Main Arena'!F2"] == "Rejected" and cells["'Main Arena'!G2"] == "9/20/2026"
    assert g.appends and g.appends[0][1] == "Zentra" and g.appends[0][5] == "Applied"
    assert {p["name"] for p in res.warm_contacts["Acme"]} == {"Remy", "Rae"}
    assert store.meta["tracker_seen_ids"] == "m1,m2"
    # second run: already-seen messages are skipped
    g2 = FakeGoogle(values, msgs)
    T.sync(g2, "sheet", {"tab": "Main Arena"}, store, now=datetime(2026, 9, 23, tzinfo=timezone.utc))
    assert not g2.appends


def test_google_client_plumbing(monkeypatch):
    import base64
    from sourcer import google as G
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "c")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "r")
    calls = []

    class Resp:
        def __init__(self, data):
            self.data, self.ok = data, True

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    b64 = lambda t: base64.urlsafe_b64encode(t.encode()).decode().rstrip("=")
    message = {"id": "m", "threadId": "t", "internalDate": "1000", "payload": {
        "headers": [{"name": "From", "value": "Jo <jo@acme.com>"}, {"name": "Subject", "value": "Hi"}],
        "mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/html", "body": {"data": b64("<p>html</p>")}},
            {"mimeType": "text/plain", "body": {"data": b64("plain body")}}]}}

    class Sess:
        def post(self, url, data=None, timeout=None):
            calls.append(("token", data["grant_type"]))
            return Resp({"access_token": "A"})

        def get(self, url, params=None, headers=None, timeout=None):
            calls.append(("get", url.rsplit("/", 1)[-1], headers["Authorization"]))
            return Resp(message if "messages/m" in url else {"messages": [{"id": "m"}]})

        def request(self, method, url, params=None, json=None, headers=None, timeout=None):
            calls.append((method, url, json))
            return Resp({})

    g = G.Google(Sess())
    assert g.gmail_search("q") == ["m"]
    m = g.gmail_message("m")
    assert m["body"] == "plain body" and m["subject"] == "Hi" and m["sender"] == "Jo <jo@acme.com>"
    g.sheet_update("S", [("'Tab'!F2", [["Rejected"]])])
    assert calls[0] == ("token", "refresh_token") and sum(c[0] == "token" for c in calls) == 1
    assert calls[-1][2]["data"][0] == {"range": "'Tab'!F2", "values": [["Rejected"]]}
    assert G.sheet_id_from("https://docs.google.com/spreadsheets/d/AbC-123_x/edit#gid=0") == "AbC-123_x"


def test_event_holds_avoid_people_conflicts():
    from datetime import timedelta
    from sourcer.assist import plan_holds
    from sourcer.sources.events import Event
    d = lambda h, day=13: datetime(2026, 10, day, h, tzinfo=timezone(timedelta(hours=-7)))
    cal = [{"summary": "Interview confirmation", "start": {"dateTime": d(17).isoformat()},
            "end": {"dateTime": d(18).isoformat()}},
           {"summary": "Coursework", "start": {"dateTime": d(19).isoformat()}, "end": {"dateTime": d(21).isoformat()}},
           {"summary": "Coffee", "transparency": "transparent", "start": {"dateTime": d(17, 14).isoformat()},
            "end": {"dateTime": d(18, 14).isoformat()}}]
    ev = lambda t, start, day=13: Event(t, f"https://luma.com/{t}", d(start, day), "SF", "", "luma",
                                       end=d(start + 2, day))
    picked, skipped = plan_holds([ev("a", 18), ev("b", 19), ev("c", 20), ev("d", 17, 14), ev("e", 17, 15)],
                                 cal, already={"luma:https://luma.com/e"})
    assert [e.title for e in picked] == ["b", "d"]            # b ok (Coursework is a self-block)
    assert [why for _, why in skipped][0].startswith("conflicts with 'Interview")
    assert any("another event that day" in w for _, w in skipped)    # c: same evening as b
