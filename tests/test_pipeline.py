import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import config
from app import db
from app import search as jobsearch
from app import tracker
from app import websearch

import run_search


def job(title="", location="", remote=False,
        url="https://example.com/jobs/some-posting-1", tags=None, company=""):
    return {"job_title": title, "company": company, "location": location,
            "url": url, "tags": tags or [], "remote": remote}


class TestRejected(unittest.TestCase):
    TRACKS = list(jobsearch.TRACKS)

    def assertTitleRejected(self, j):
        self.assertIsNotNone(jobsearch._title_reject(j), j["job_title"])

    def assertTitleOk(self, j):
        self.assertIsNone(jobsearch._title_reject(j), j["job_title"])

    def test_skill_rejects(self):
        for title in ["Java Developer", "Golang Engineer", "Go Developer",
                      "C++ Engineer", "C# Developer", ".NET Developer"]:
            self.assertTitleRejected(job(title=title))

    def test_skill_lookalikes_pass(self):
        for title in ["JavaScript Developer", "React Developer",
                      "Typescript Frontend Engineer"]:
            self.assertTitleOk(job(title=title))

    def test_seniority_boundary_rejects(self):
        for title in ["Sr. Frontend Developer", "SR Frontend Developer",
                      "Senior Frontend Developer"]:
            self.assertTitleRejected(job(title=title))

    def test_boundary_lookalikes_pass(self):
        for title in ["SRE Engineer", "C10 Developer"]:
            self.assertTitleOk(job(title=title))

    def test_rejected_host(self):
        self.assertIsNotNone(jobsearch._hygiene_reject(job(
            title="Frontend Engineer",
            url="https://www.ziprecruiter.com/jobs/frontend-engineer-1")))

    def test_place_text_rejects(self):
        for title in ["Boulder Valley School District Teacher",
                      "BVSD Para Educator", "City of Boulder Colorado Staff",
                      "Columbus Climbing Instructor", "Ohio Gym Manager"]:
            reason, _ = jobsearch._location_reject(
                job(title=title), self.TRACKS)
            self.assertIsNotNone(reason, title)

    def test_bouldering_berlin_passes(self):
        for title in ["Boulderwelt Berlin Route Setter",
                      "Bouldering Coach Berlin"]:
            reason, _ = jobsearch._location_reject(
                job(title=title, location="Berlin"), self.TRACKS)
            self.assertIsNone(reason, title)

    def test_question_and_seo_titles(self):
        for title in ["What is a Frontend Engineer",
                      "How to become a developer",
                      "Frontend Developer Jobs in Berlin",
                      "Software Engineer jobs"]:
            self.assertIsNotNone(
                jobsearch._hygiene_reject(job(title=title)), title)

    def test_listing_page_titles(self):
        for title in ["193 Node.js-Jobs | Startup Insider",
                      "FULL STACK jobs | London, United Kingdom | Job-in UK",
                      "Remote Node.js Jobs - NoDesk",
                      "Bartender Jobs London: Apply Now | Fraser Bond",
                      "18523 Application Support Job Vacancies In Germany",
                      "Frontend Development Remote in Deutschland: 40 offene Stellen",
                      "Manage Gritstone– Climbing Jobs Weekly 2022 August 25",
                      "typescript", "React Native", "Career"]:
            self.assertEqual(jobsearch._hygiene_reject(job(title=title)),
                             "Multi-job listing", title)

    def test_content_page_titles(self):
        self.assertEqual(
            jobsearch._hygiene_reject(job(
                title="Boulderhalle in Berlin Pankow - Sport und Fitness Berlin Guide")),
            "Non-job content page")

    def test_real_titles_not_listing(self):
        for title in ["Routesetter", "Bartender",
                      "UKC News - JOBS: Climbing Team Member (Bouldering)",
                      "Frontend Developer (m/f/d)",
                      "Junior React Engineer at Jobsuche GmbH"]:
            self.assertIsNone(jobsearch._hygiene_reject(job(title=title)), title)

    def test_rejected_host_is_suffix_match(self):
        self.assertIsNone(jobsearch._hygiene_reject(job(
            title="System Administrator",
            url="https://jobs.ashbyhq.com/taxfix.com/881e6d21")))
        self.assertIsNone(jobsearch._hygiene_reject(job(
            title="System Administrator", url="https://taxfix.com/jobs/1")))
        self.assertIsNotNone(jobsearch._hygiene_reject(job(
            title="System Administrator", url="https://x.com/someone/status/1")))
        self.assertIsNotNone(jobsearch._hygiene_reject(job(
            title="Frontend Engineer", url="https://de.linkedin.com/jobs/view/1")))


class TestFetchWebsearch(unittest.TestCase):
    RESULTS = [
        {"title": "Climbing Instructor Columbus Ohio",
         "url": "https://example.com/job/1", "snippet": "Gym role"},
        {"title": "Frontend Engineer Berlin",
         "url": "https://example.com/job/2", "snippet": "Build UI"},
        {"title": "Remote Frontend Engineer Germany",
         "url": "https://example.com/job/3", "snippet": ""},
    ]

    def test_geo_from_evidence_not_query(self):
        queries = []

        def fake_search(query, limit=10):
            queries.append(query)
            return list(self.RESULTS)

        with patch.object(websearch, "search", fake_search):
            jobs = list(jobsearch._fetch_websearch())

        expected = sum(
            len(cfg.get("search_terms") or [cfg["keywords"][0]])
            * len(config.search_targets(cfg["remote"]))
            for cfg in jobsearch.TRACKS.values())
        self.assertEqual(len(queries), expected)
        for q in queries:
            self.assertTrue(any(t in q for t in config.search_targets(True)),
                            f"no geo target in query {q!r}")
        self.assertIn("application support engineer jobs Berlin", queries)
        self.assertIn("application support engineer jobs United Kingdom",
                      queries)
        self.assertIn("bouldering gym jobs Berlin", queries)
        self.assertNotIn("bouldering gym jobs jobs Berlin", queries)
        self.assertIn("frontend developer remote jobs Berlin", queries)
        self.assertIn("react native developer remote jobs Berlin", queries)
        self.assertIn("full stack developer remote jobs Berlin", queries)
        self.assertIn("node.js developer remote jobs United Kingdom",
                      queries)
        for q in queries:
            self.assertNotIn("jobs remote jobs", q)

        by_title = {}
        for j in jobs:
            by_title.setdefault(j["job_title"], j)

        columbus = by_title["Climbing Instructor Columbus Ohio"]
        self.assertEqual(columbus["location"], "")
        self.assertFalse(columbus["remote"])
        reason, _ = jobsearch._location_reject(
            columbus, list(jobsearch.TRACKS))
        self.assertIsNotNone(reason)

        berlin = by_title["Frontend Engineer Berlin"]
        self.assertEqual(berlin["location"], "Berlin")
        self.assertFalse(berlin["remote"])

        germany = by_title["Remote Frontend Engineer Germany"]
        self.assertEqual(germany["location"], "Germany")
        self.assertTrue(germany["remote"])

        self.assertTrue(all(j["_source"] == "websearch" for j in jobs))

    def test_bouldering_search_terms(self):
        cfg = jobsearch.TRACKS["Bouldering Gyms"]
        queries = []

        def fake_search(query, limit=10):
            queries.append(query)
            return []

        with patch.object(jobsearch, "TRACKS",
                          {"Bouldering Gyms": cfg}), \
                patch.object(websearch, "search", fake_search):
            list(jobsearch._fetch_websearch())

        terms = cfg["search_terms"]
        targets = config.search_targets(cfg["remote"])
        self.assertEqual(len(queries), len(terms) * len(targets))
        self.assertEqual(len(queries), 12)
        for term in terms:
            self.assertIn(f"{term} Berlin", queries)
            self.assertIn(f"{term} United Kingdom", queries)


class _FakeResp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class TestHtmlBoards(unittest.TestCase):
    HTML = '<html><body><a href="/jobs/frontend-engineer-1">' \
           'Frontend Engineer</a></body></html>'

    def test_board_scope_verbatim(self):
        boards = [{
            "name": "TestBoard",
            "url": "https://board.example.com/jobs",
            "location": "United Kingdom",
            "remote": False,
        }]
        with patch.object(jobsearch, "ADDITIONAL_BOARDS", boards), \
             patch.object(jobsearch.requests, "get",
                          lambda *a, **k: _FakeResp(self.HTML)):
            jobs = list(jobsearch._fetch_html_boards())

        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["location"], "United Kingdom")
        self.assertFalse(j["remote"])
        self.assertEqual(j["_source"], "html:TestBoard")
        self.assertNotIn(j["location"], config.cities() + ["Remote"])

    BOARDS = [
        {"name": "B1", "url": "https://b1.example.com/jobs",
         "location": "Berlin", "remote": False},
        {"name": "B2", "url": "https://b2.example.com/jobs",
         "location": "Berlin", "remote": False},
    ]

    def test_all_boards_failed_raises(self):
        def boom(*a, **k):
            raise ConnectionError("down")

        with patch.object(jobsearch, "ADDITIONAL_BOARDS", self.BOARDS), \
                patch.object(jobsearch.requests, "get", boom):
            with self.assertRaises(RuntimeError):
                list(jobsearch._fetch_html_boards())

    def test_partial_board_failure_ok(self):
        def fake_get(url, **k):
            if "b1" in url:
                raise ConnectionError("down")
            return _FakeResp(self.HTML)

        with patch.object(jobsearch, "ADDITIONAL_BOARDS", self.BOARDS), \
                patch.object(jobsearch.requests, "get", fake_get):
            jobs = list(jobsearch._fetch_html_boards())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["_source"], "html:B2")


class TestMinedCandidate(unittest.TestCase):
    LINK = {"title": "React Developer - Acme",
            "url": "https://boards.greenhouse.io/acme/jobs/react-dev-1"}

    def mine(self, **scope):
        return jobsearch._mined_candidate(
            dict(self.LINK), ["Developer"], set(), set(), **scope)

    def test_configured_scope_passes(self):
        cand = self.mine(location="Berlin", remote=False, source="extract")
        self.assertIsNotNone(cand)
        self.assertEqual(cand["location"], "Berlin")
        self.assertNotEqual(cand["location"], "Remote")
        self.assertEqual(cand["_source"], "extract")
        self.assertIn("source: extract", cand["why_fit"])

    def test_empty_scope_fails(self):
        self.assertIsNone(self.mine(location="", remote=False,
                                    source="extract"))

    def test_remote_only_scope_fails(self):
        self.assertIsNone(self.mine(location="Remote", remote=True,
                                    source="extract"))


class TestCollectWithReport(unittest.TestCase):
    def _patch_pipeline(self, adapters, get_jobs=None, declined=None,
                        dup=None):
        return [
            patch.object(jobsearch, "_adapters", lambda track=None: adapters),
            patch.object(jobsearch, "_websearch_enabled", lambda: False),
            patch.object(jobsearch, "_check_links", lambda c: c),
            patch.object(tracker, "get_jobs",
                         get_jobs or (lambda strict=False: [])),
            patch.object(tracker, "declined_domains",
                         declined or (lambda jobs=None: set())),
            patch.object(tracker, "has_duplicate",
                         dup or (lambda c, jobs=None: None)),
        ]

    def test_counters(self):
        def fake_fetch():
            yield job(title="React Developer", location="Berlin",
                      url="https://example.com/jobs/react-1",
                      company="Acme") | {"_source": "fake"}
            yield job(title="React Developer", location="New York",
                      url="https://example.com/jobs/react-2",
                      company="Acme") | {"_source": "fake"}

        patches = self._patch_pipeline([("fake", fake_fetch)])
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5]:
            result = jobsearch.collect_with_report()

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.report.fetched, {"fake": 2})
        self.assertEqual(result.report.accepted, {"fake": 1})
        self.assertEqual(result.report.rejected,
                         {"location": {"No geo evidence": 1}})
        self.assertEqual(result.report.funnel,
                         {"fetched": 2, "hygiene": 2, "location": 1,
                          "title": 1, "fit": 1})
        self.assertEqual(result.report.errors, {})
        rendered = json.loads(result.report.render())
        self.assertEqual(set(rendered),
                         {"fetched", "accepted", "rejected", "funnel",
                          "errors"})
        self.assertEqual(result.report.render(), result.report.render())

    def test_missing_vs_duplicate_url(self):
        def fake_fetch():
            yield job(title="React Developer", location="Berlin",
                      url="", company="A") | {"_source": "fake"}
            yield job(title="React Developer", location="Berlin",
                      url="https://example.com/jobs/same",
                      company="A") | {"_source": "fake"}
            yield job(title="React Developer", location="Berlin",
                      url="https://example.com/jobs/same",
                      company="B") | {"_source": "fake"}

        patches = self._patch_pipeline([("fake", fake_fetch)])
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5]:
            result = jobsearch.collect_with_report()

        self.assertEqual(result.report.fetched, {"fake": 3})
        self.assertEqual(result.report.accepted, {"fake": 1})
        self.assertEqual(result.report.rejected["hygiene"]["Missing URL"], 1)
        self.assertEqual(result.report.rejected["hygiene"]["Duplicate URL"],
                         1)

    def test_single_db_snapshot(self):
        snapshot = [{"job_title": "existing", "status": "New"}]
        calls = {"get_jobs": 0}
        seen = {"declined": [], "dup": []}

        def fake_get_jobs(strict=False):
            calls["get_jobs"] += 1
            self.assertTrue(strict)
            return snapshot

        def fake_fetch():
            yield job(title="React Developer", location="Berlin",
                      url="https://example.com/jobs/react-9",
                      company="Acme") | {"_source": "fake"}

        patches = self._patch_pipeline(
            [("fake", fake_fetch)],
            get_jobs=fake_get_jobs,
            declined=lambda jobs=None: seen["declined"].append(jobs) or set(),
            dup=lambda c, jobs=None: seen["dup"].append(list(jobs)) or None)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5]:
            result = jobsearch.collect_with_report()

        self.assertEqual(calls["get_jobs"], 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(seen["declined"], [snapshot])
        self.assertEqual(seen["dup"], [snapshot])

    def test_invalid_track_fails_before_db(self):
        def spy(strict=False):
            raise AssertionError("get_jobs must not run for unknown track")

        patches = self._patch_pipeline([], get_jobs=spy)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5]:
            with self.assertRaisesRegex(ValueError,
                                        "Unknown track: Nope"):
                jobsearch.collect_with_report(track="Nope")

    def test_all_sources_failed(self):
        def bad():
            raise ConnectionError("down")
            yield

        patches = self._patch_pipeline([("bad1", bad), ("bad2", bad)])
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5]:
            with self.assertRaises(RuntimeError):
                jobsearch.collect_with_report()

    def test_partial_yield_not_all_failed(self):
        def flaky():
            yield job(title="React Developer", location="Berlin",
                      url="https://example.com/jobs/react-7",
                      company="Acme") | {"_source": "flaky"}
            raise ConnectionError("cut mid-stream")

        def bad():
            raise ConnectionError("down")
            yield

        patches = self._patch_pipeline([("flaky", flaky), ("bad", bad)])
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5]:
            result = jobsearch.collect_with_report()

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.report.accepted, {"flaky": 1})
        self.assertEqual(set(result.report.errors), {"flaky", "bad"})

    def test_arbeitnow_error_recorded(self):
        def ok_fetch():
            yield job(title="React Developer", location="Berlin",
                      url="https://x.example.com/jobs/r1",
                      company="A") | {"_source": "ok"}

        def boom(*a, **k):
            raise ConnectionError("offline")

        patches = self._patch_pipeline(
            [("arbeitnow", jobsearch._fetch_arbeitnow), ("ok", ok_fetch)])
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5], patch.object(jobsearch, "_get_json", boom):
            result = jobsearch.collect_with_report()

        self.assertIn("arbeitnow", result.report.errors)
        self.assertEqual(len(result.candidates), 1)

    def test_same_run_duplicates(self):
        real_dup = tracker.has_duplicate

        def fake_fetch():
            rows = [
                ("React Developer", "Acme", "https://a.example.com/jobs/r1"),
                ("React Developer", "Acme", "https://b.example.com/jobs/r2"),
                ("Backend Engineer", "Other", "https://c.example.com/jobs/r3"),
            ]
            for title, company, url in rows:
                yield job(title=title, location="Berlin", url=url,
                          company=company) | {"_source": "fake"}

        patches = self._patch_pipeline(
            [("fake", fake_fetch)],
            dup=lambda c, jobs=None: real_dup(c, jobs))
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
                patches[5]:
            result = jobsearch.collect_with_report()

        self.assertEqual(len(result.candidates), 3)
        flags = [c["_dup_flag"] for c in result.candidates]
        self.assertEqual(flags, [False, False, True])
        self.assertEqual(
            [c["url"] for c in result.candidates if c["_dup_flag"]],
            ["https://b.example.com/jobs/r2"])

    def test_optional_adapters_gated(self):
        with patch.dict(os.environ, {"ADZUNA_APP_ID": "",
                                     "ADZUNA_APP_KEY": "",
                                     "REED_API_KEY": ""}):
            names = [n for n, _ in jobsearch._adapters()]
            self.assertNotIn("adzuna", names)
            self.assertNotIn("reed", names)
        with patch.dict(os.environ, {"ADZUNA_APP_ID": "x",
                                     "ADZUNA_APP_KEY": "y",
                                     "REED_API_KEY": "z"}):
            names = [n for n, _ in jobsearch._adapters()]
            self.assertIn("adzuna", names)
            self.assertIn("reed", names)


class TestNewTracks(unittest.TestCase):
    def first_track(self, title, location="Berlin", remote=False,
                    company=""):
        j = job(title=title, location=location, remote=remote,
                company=company)
        cand, _ = jobsearch._evaluate(
            j, j["url"], "test", list(jobsearch.TRACKS),
            jobsearch.SearchReport())
        return cand["track"] if cand else None

    def test_graduate_consultant_track(self):
        self.assertEqual(
            self.first_track(
                "Associate Consultant - Entry-Level Technology Consulting"),
            "Graduate Technology Consultant")

    def test_security_trainee_track(self):
        self.assertEqual(
            self.first_track(
                "Trainee Information Technology Security (m/w/d) "
                "ab 01.04.2027"),
            "IT Security Trainee")
        self.assertEqual(
            self.first_track("Graduate Cybersecurity Analyst"),
            "IT Security Trainee")

    def test_senior_consultant_rejected(self):
        j = job(title="Senior Technology Consultant", location="Berlin")
        self.assertIsNotNone(jobsearch._title_reject(j))
        self.assertIsNone(self.first_track("Senior Technology Consultant"))

    def test_generic_titles_no_match(self):
        for title in ["Management Consultant", "Security Guard"]:
            self.assertNotIn(
                self.first_track(title),
                ("Graduate Technology Consultant", "IT Security Trainee"))

    def test_geo_remote_tracks(self):
        for track in ("Graduate Technology Consultant",
                      "IT Security Trainee"):
            cfg = jobsearch.TRACKS[track]
            self.assertTrue(cfg["remote"])
            reason, eligible = jobsearch._location_reject(
                job(location="Berlin"), [track])
            self.assertIsNone(reason, track)
            self.assertEqual(eligible, [track])
            for loc in ["Remote - Germany", "Remote - UK"]:
                reason, eligible = jobsearch._location_reject(
                    job(location=loc, remote=True), [track])
                self.assertIsNone(reason, loc)
                self.assertEqual(eligible, [track])
            for loc in ["Remote - US", "Remote - Canada"]:
                reason, _ = jobsearch._location_reject(
                    job(location=loc, remote=True), [track])
                self.assertIsNotNone(reason, loc)

    def test_bouldering_role_titles(self):
        for title in ["Routesetter Berlin",
                      "Routenschrauber (m/w/d) Berlin",
                      "Bouldering Coach Berlin"]:
            self.assertEqual(self.first_track(title), "Bouldering Gyms",
                             title)

    def test_bouldering_known_gym_company(self):
        self.assertEqual(
            self.first_track("Front Desk Mitarbeiter",
                             company="Element Boulders Berlin"),
            "Bouldering Gyms")
        self.assertNotEqual(
            self.first_track("Front Desk Mitarbeiter",
                             company="Generic Hotel"),
            "Bouldering Gyms")
        self.assertEqual(
            self.first_track("Service/Theke", company="Berta Block"),
            "Bouldering Gyms")
        self.assertNotEqual(
            self.first_track("Service/Theke",
                             company="Generic Cafe Mitte"),
            "Bouldering Gyms")

    def test_bare_climbing_no_match(self):
        self.assertNotEqual(
            self.first_track("Climbing Instructor",
                             company="Summer Camp"),
            "Bouldering Gyms")

    def test_bouldering_hours_rejected(self):
        for title in ["Minijob Bouldering Coach Berlin",
                      "Teilzeit Routesetter Berlin"]:
            self.assertIsNotNone(jobsearch._title_reject(
                job(title=title, location="Berlin")), title)


class TestContextualMatch(unittest.TestCase):
    CFG = jobsearch.TRACKS["Bouldering Gyms"]

    def test_known_gym_role_matches(self):
        hits = jobsearch._matches(
            job(title="Front Desk Mitarbeiter",
                company="Element Boulders Berlin"), self.CFG)
        self.assertIn("front desk", hits)

    def test_generic_company_role_fails(self):
        self.assertEqual(jobsearch._matches(
            job(title="Front Desk Mitarbeiter", company="Generic Hotel"),
            self.CFG), [])

    def test_junk_titles_at_known_gym_fail(self):
        for company in ["Berta Block", "Element Boulders Berlin"]:
            for title in ["Kurse", "Kontakt", "Preise", "Jobs"]:
                self.assertEqual(jobsearch._matches(
                    job(title=title, company=company), self.CFG), [],
                    f"{title} at {company}")

    def test_role_cannot_come_from_company(self):
        self.assertEqual(jobsearch._matches(
            job(title="Kurse", company="Front Desk Element Boulders"),
            self.CFG), [])

    def test_direct_keyword_still_matches(self):
        hits = jobsearch._matches(job(title="Routesetter Berlin"), self.CFG)
        self.assertIn("routesetter", hits)

    def test_full_stack_developer_matches_developer_track(self):
        hits = jobsearch._matches(job(title="Full Stack Developer"),
                                  jobsearch.TRACKS["Developer"])
        self.assertIn("full stack", hits)

    def test_mobile_application_developer_matches_frontend_track(self):
        hits = jobsearch._matches(
            job(title="Mobile Application Developer"),
            jobsearch.TRACKS["Frontend Engineer"])
        self.assertIn("mobile application developer", hits)

    def test_gsap_developer_matches_frontend_track(self):
        hits = jobsearch._matches(job(title="GSAP Developer"),
                                  jobsearch.TRACKS["Frontend Engineer"])
        self.assertIn("gsap", hits)


class TestResumeFit(unittest.TestCase):
    CV = "Fullstack/Frontend CV"
    ALL = list(jobsearch.TRACKS)

    def fit(self, text, cv=None):
        return jobsearch._resume_fit(text, cv or self.CV)

    def evaluate(self, j):
        report = jobsearch.SearchReport()
        cand, _ = jobsearch._evaluate(
            j, j.get("url", ""), "test", self.ALL, report)
        return cand, report

    def test_full_match(self):
        f = self.fit("React TypeScript GraphQL Docker")
        self.assertEqual(f.have, ["react", "typescript", "graphql", "docker"])
        self.assertEqual(f.gaps, [])
        self.assertEqual(f.ratio, 1.0)

    def test_gap_heavy_rejected(self):
        f = self.fit("Java Spring Boot Kotlin AWS React")
        self.assertEqual(f.have, ["react"])
        self.assertEqual(len(f.gaps), 4)
        self.assertAlmostEqual(f.ratio, 0.2)
        j = job(title="Software Engineer", location="Berlin")
        j["description"] = "Java Spring Boot Kotlin AWS React"
        cand, report = self.evaluate(j)
        self.assertIsNone(cand)
        self.assertEqual(report.rejected,
                         {"fit": {"Low resume match": 1}})

    def test_unknown_fit_not_dropped(self):
        f = self.fit("React Developer")
        self.assertIsNone(f.ratio)
        cand, report = self.evaluate(
            job(title="React Developer", location="Berlin"))
        self.assertIsNotNone(cand)
        self.assertIsNone(cand["fit"]["ratio"])

    def test_alias_dedupe(self):
        f = self.fit("Node.js and NodeJS")
        self.assertEqual(f.have, ["node.js"])
        self.assertIsNone(f.ratio)

    def test_javascript_not_java(self):
        f = self.fit("JavaScript Developer")
        self.assertIn("javascript", f.have)
        self.assertNotIn("java", f.gaps)

    def test_stage_ordering(self):
        cand, report = self.evaluate(
            job(title="Java Developer", location="New York"))
        self.assertIsNone(cand)
        self.assertEqual(report.rejected,
                         {"location": {"No geo evidence": 1}})
        cand, report = self.evaluate(
            job(title="Java Developer", location="Berlin"))
        self.assertIsNone(cand)
        self.assertEqual(report.rejected,
                         {"title": {"Skill: java": 1}})

    def test_score_examples(self):
        cfg = jobsearch.TRACKS["Developer"]
        j = job(title="React Developer", remote=True)
        def score(have, gaps, hits=("react",)):
            return jobsearch._score(list(hits), j, cfg,
                                    jobsearch.Fit(have, gaps, None))
        # 1 base + 1 hit + 1 remote + 6 * (have+1)/(detected+2)
        self.assertEqual(score([], []), 6.0)            # no evidence: neutral
        self.assertEqual(score(["react"], []), 7.0)
        self.assertEqual(score(["react", "typescript", "css", "git",
                                "redux"], ["angular"]), 7.5)
        self.assertEqual(score([], ["java", "aws", "kotlin"]), 4.2)
        self.assertEqual(score(["react"], [], hits=("a", "b", "c")), 8.0)
        self.assertEqual(jobsearch._score(
            ["react"], job(title="React Developer", location="Munich"), cfg,
            jobsearch.Fit([], [], None)), 5.0)        # no geo bonus

    def test_score_spreads(self):
        # More evidence moves the score further from neutral in both directions.
        strong = jobsearch.score_from(1, 10, 1, True)
        weak = jobsearch.score_from(1, 1, 0, True)
        unknown = jobsearch.score_from(1, 0, 0, True)
        poor = jobsearch.score_from(1, 1, 6, True)
        self.assertGreater(strong, weak)
        self.assertGreater(weak, unknown)
        self.assertGreater(unknown, poor)
        self.assertLessEqual(jobsearch.score_from(9, 99, 0, True), 10)

    def test_config_validation(self):
        data = json.loads((ROOT / "config.json").read_text())
        bad_cv = json.loads(json.dumps(data))
        bad_cv["tracks"]["Developer"]["cv"] = "Nope CV"
        with self.assertRaisesRegex(ValueError, "config.tracks.Developer.cv"):
            config.validate(bad_cv)
        bad_group = json.loads(json.dumps(data))
        bad_group["resume"]["cvs"]["Frontend CV"]["have"].append("nope")
        with self.assertRaisesRegex(ValueError, "unknown skill group"):
            config.validate(bad_group)

    def test_verify_stage(self):
        def cand(url, title, hits):
            j = job(title=title, remote=True)
            return jobsearch._candidate(
                j, url, "Developer", hits,
                jobsearch.Fit([], [], None), "test")

        cands = [
            cand("https://a.example.com/low", "Software Engineer",
                 ["software engineer"]),
            cand("https://b.example.com/high", "React Developer", ["react"]),
        ]
        bodies = {
            "https://a.example.com/low": "Java Spring Boot Kotlin AWS",
            "https://b.example.com/high":
                "React TypeScript GraphQL Docker CI/CD",
        }
        report = jobsearch.SearchReport()
        with patch.object(jobsearch, "_websearch_enabled", lambda: True), \
                patch.object(jobsearch.websearch, "requests", object()), \
                patch.object(websearch, "extract_content",
                             lambda url, objective: bodies.get(url, "")):
            out = jobsearch._verify_candidates(cands, report)

        self.assertEqual([c["url"] for c in out],
                         ["https://b.example.com/high"])
        self.assertEqual(report.rejected,
                         {"fit": {"Low resume match (verified)": 1}})
        high = out[0]
        self.assertTrue(high["_verified"])
        self.assertEqual(high["fit"]["ratio"], 1.0)
        # 1 + 1 hit + 1 remote + 6 * (5+1)/(5+2) for react/ts/graphql/docker/ci
        self.assertEqual(high["match_score"], 8.1)

    def test_arbeitnow_description_stripped(self):
        payload = {"data": [{
            "title": "Frontend Dev", "company_name": "Acme",
            "location": "Berlin", "url": "https://a.example.com/j/1",
            "tags": [], "remote": False,
            "description": "<p>Build <b>React</b> apps &amp; more</p>",
        }]}
        with patch.object(jobsearch, "_get_json", lambda url: payload):
            jobs = list(jobsearch._fetch_arbeitnow())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["description"], "Build React apps & more")


class TestLanguageLevel(unittest.TestCase):
    def check(self, text):
        return jobsearch._language_check(text)

    def evaluate(self, j):
        report = jobsearch.SearchReport()
        cand, _ = jobsearch._evaluate(
            j, j.get("url", ""), "test", list(jobsearch.TRACKS), report)
        return cand, report

    def test_rejects(self):
        for text, reason in [
            ("German C1", "Language: German C1"),
            ("Deutschkenntnisse auf C1-Niveau", "Language: German C1"),
            ("sehr gute Deutschkenntnisse (mind. C1)",
             "Language: German C1"),
            ("Deutsch: C1, Englisch: B2", "Language: German C1"),
            ("German C2", "Language: German C2"),
            ("Fluent German required", "Language: fluent german"),
        ]:
            self.assertEqual(self.check(text).reason, reason, text)

    def test_passes(self):
        for text, level in [
            ("German B1", "B1"),
            ("Deutsch B2", "B2"),
            ("German B1.2", "B1"),
            ("German B2-C1", "B2"),
            ("Englisch C1, Deutsch B1", "B1"),
            ("German required (B1)", "B1"),
            ("English C1", None),
            ("German C1 is a plus", None),
            ("Fluent German is a nice to have", None),
            ("Based in Germany, office C1 building", None),
        ]:
            r = self.check(text)
            self.assertIsNone(r.reason, text)
            self.assertEqual(r.level, level, text)

    def test_bare_level_in_title_passes(self):
        self.assertIsNone(jobsearch._title_reject(
            job(title="Frontend Developer (C1)")))

    def test_description_fit_stage(self):
        j = job(title="React Developer", location="Berlin")
        j["description"] = "Deutsch C1 erforderlich"
        cand, report = self.evaluate(j)
        self.assertIsNone(cand)
        self.assertEqual(report.rejected,
                         {"fit": {"Language: German C1": 1}})

        j["description"] = "Deutschkenntnisse B2"
        cand, report = self.evaluate(j)
        self.assertIsNotNone(cand)
        self.assertEqual(cand["fit"]["german"], "B2")
        self.assertIn("German: B2", cand["why_fit"])

    def test_verify_stage(self):
        def cand(url, title):
            return jobsearch._candidate(
                job(title=title, remote=True), url, "Developer",
                ["react"], jobsearch.Fit([], [], None), "test")

        cands = [cand("https://a.example.com/c1", "React Developer"),
                 cand("https://b.example.com/b2", "React Developer")]
        bodies = {"https://a.example.com/c1": "German C1",
                  "https://b.example.com/b2": "German B2"}
        with patch.object(jobsearch, "_websearch_enabled", lambda: True), \
                patch.object(jobsearch.websearch, "requests", object()), \
                patch.object(websearch, "extract_content",
                             lambda url, objective: bodies.get(url, "")):
            out = jobsearch._verify_candidates(
                cands, jobsearch.SearchReport())
        self.assertEqual([c["url"] for c in out],
                         ["https://b.example.com/b2"])
        self.assertEqual(out[0]["fit"]["german"], "B2")
        self.assertIn("German: B2", out[0]["why_fit"])

    def test_config_validation(self):
        data = json.loads((ROOT / "config.json").read_text())
        bad = json.loads(json.dumps(data))
        bad["reject"]["language_level"]["max_level"] = "D1"
        with self.assertRaisesRegex(ValueError, "max_level"):
            config.validate(bad)


class TestDbFetchAll(unittest.TestCase):
    def test_fetch_error_raises(self):
        class _Boom:
            def table(self, name):
                raise ConnectionError("db down")

        with patch.object(db, "ENABLED", True), \
                patch.object(db, "client", _Boom()):
            with self.assertRaisesRegex(RuntimeError, "fetch_all failed"):
                db.fetch_all()

    def test_fetch_all_paginates_past_page_cap(self):
        calls = []

        class _Q:
            def select(self, *a, **k):
                return self

            def range(self, s, e):
                self._s, self._e = s, e
                return self

            def execute(self):
                calls.append((self._s, self._e))
                rows = [{"job_title": f"j{i}", "id": i}
                        for i in range(self._s, min(self._e + 1, 1205))]
                return type("_Resp", (), {"data": rows})()

        class _Client2:
            def table(self, name):
                return _Q()

        with patch.object(db, "ENABLED", True), \
                patch.object(db, "client", _Client2()):
            jobs = db.fetch_all()
        self.assertEqual(len(jobs), 1205)
        self.assertEqual(calls, [(0, 999), (1000, 1999)])

    def test_insert_duplicate_url_reports_not_raises(self):
        class _Q:
            def insert(self, row):
                return self

            def execute(self):
                raise Exception(
                    "duplicate key value violates unique constraint "
                    '"jobs_url_key" (23505)')

        class _Client:
            def table(self, name):
                return _Q()

        with patch.object(db, "ENABLED", True), \
                patch.object(db, "client", _Client()):
            self.assertEqual(db.insert({"job_title": "x", "url": "u"}),
                             "duplicate")
            ok, msg = tracker.add_job(
                {"job_title": "x", "url": "u"}, check_duplicate=False)
            self.assertFalse(ok)
            self.assertIn("Duplicate", msg)

    def test_fetch_disabled_returns_empty(self):
        with patch.object(db, "ENABLED", False), \
                patch.object(db, "client", None):
            self.assertEqual(db.fetch_all(), [])

    def test_strict_get_jobs_requires_db(self):
        with patch.object(db, "ENABLED", False):
            with self.assertRaisesRegex(RuntimeError,
                                        "Supabase not configured"):
                tracker.get_jobs(strict=True)
            self.assertEqual(tracker.get_jobs(), [])


class TestRunSearch(unittest.TestCase):
    def test_run_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            with patch.object(run_search, "REPORT_PATH", report_path), \
                    patch.object(jobsearch, "collect_with_report",
                                 side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    run_search.run()

    def test_run_skips_recheck(self):
        cand = job(title="React Developer", location="Berlin")
        result = jobsearch.SearchResult(
            candidates=[cand], report=jobsearch.SearchReport())
        added = []

        def fake_add(c, check_duplicate=True):
            added.append(check_duplicate)
            return True, "ok"

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            with patch.object(run_search, "REPORT_PATH", report_path), \
                    patch.object(jobsearch, "collect_with_report",
                                 lambda track=None: result), \
                    patch.object(tracker, "add_job", fake_add):
                run_search.run()
            self.assertEqual(added, [False])
            self.assertTrue(report_path.exists())
            report = json.loads(report_path.read_text())
            self.assertIn("funnel", report)
            self.assertIn("generated_at", report)


class TestBuildStatic(unittest.TestCase):
    def test_strict_read(self):
        import build_static

        def boom(strict=False):
            raise RuntimeError("Supabase not configured")

        env = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_KEY_PUBLIC": "sb_publishable_test"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(build_static.tracker, "get_jobs", boom):
            with self.assertRaises(RuntimeError):
                build_static.build()


if __name__ == "__main__":
    unittest.main()


class TestLocationLabels(unittest.TestCase):
    def test_groups(self):
        cases = {
            "Berlin, Germany": "Berlin",
            "Berlin (Hybrid)": "Berlin / Remote",
            "London": "London",
            "Glasgow, Scotland, United Kingdom": "United Kingdom",
            "Zilch Uk": "United Kingdom",
            "Edinburgh": "United Kingdom",
            "Munich, Bayern, Germany": "Germany",
            "Bielefeld": "Germany",
            "Remote - Germany": "Germany / Remote",
            "Home Office Deutschland": "Germany / Remote",
            "Poste À Distance": "Remote",
            "Usa, Canada, Argentina": "Other",
            "": "Unspecified",
        }
        for raw, label in cases.items():
            self.assertEqual(tracker._normalize_location(raw), label, raw)

    def test_display_terms_do_not_widen_geo(self):
        # "Munich" labels as Germany but is not geo evidence for the filter.
        self.assertNotIn("munich", [t for g in config.geo_groups()
                                    for t in g.get("terms", [])])


class TestRescore(unittest.TestCase):
    def setUp(self):
        import rescore
        self.rescore = rescore.rescore

    def test_new_format(self):
        j = {"track": "Developer", "job_title": "React Developer",
             "location": "Remote",
             "why_fit": "Title: react; Fit: 5/6 (have: a; missing: b); remote"}
        self.assertEqual(self.rescore(j), jobsearch.score_from(1, 5, 1, True))

    def test_legacy_format_rechecks_title(self):
        j = {"track": "Developer", "location": "London",
             "job_title": "React TypeScript Developer",
             "why_fit": "Matches: react, typescript; source: websearch"}
        self.assertEqual(self.rescore(j), jobsearch.score_from(2, 2, 0, False))

    def test_unknown_track_skipped(self):
        self.assertIsNone(self.rescore({"track": "Nope", "why_fit": ""}))


class TestForwardDeployedTrack(unittest.TestCase):
    def match(self, title):
        return next((name for name, cfg in jobsearch.TRACKS.items()
                     if jobsearch._matches(job(title=title), cfg)), None)

    def test_fde_titles(self):
        for title in ["Forward Deployed Engineer",
                      "Forward-Deployed Software Engineer, New Grad",
                      "FDE - London",
                      "Forward Deployed Frontend Engineer",   # wins over Frontend
                      "Deployment Strategist"]:
            self.assertEqual(self.match(title), "Forward Deployed Engineer", title)

    def test_senior_fde_rejected(self):
        self.assertIsNotNone(jobsearch._title_reject(
            job(title="Senior Forward Deployed Engineer")))


class TestAtsFeeds(unittest.TestCase):
    PAYLOADS = {
        "greenhouse": {"jobs": [{
            "title": "Frontend Engineer", "company_name": "Monzo",
            "location": {"name": "London, United Kingdom"},
            "absolute_url": "https://job-boards.greenhouse.io/monzo/jobs/1",
            "departments": [{"name": "Engineering"}],
            "content": "&lt;p&gt;React &amp;amp; TypeScript&lt;/p&gt;"}]},
        "lever": [{
            "text": "Forward Deployed Engineer",
            "categories": {"location": "London, United Kingdom", "team": "Eng"},
            "hostedUrl": "https://jobs.lever.co/palantir/1",
            "workplaceType": "onsite", "descriptionPlain": "Python and React"}],
        "ashby": {"jobs": [
            {"title": "Support Engineer", "location": "Berlin",
             "secondaryLocations": [{"location": "Remote - Germany"}],
             "jobUrl": "https://jobs.ashbyhq.com/n8n/1", "isRemote": True,
             "isListed": True, "descriptionPlain": "Linux"},
            {"title": "Hidden", "isListed": False, "jobUrl": "x"}]},
        "workable": {"name": "Acme", "jobs": [{
            "title": "QA Engineer", "city": "Berlin", "country": "Germany",
            "url": "https://apply.workable.com/acme/j/1", "telecommuting": False}]},
        "smartrecruiters": {"content": [{
            "id": "77", "name": "IT Support", "company": {"name": "Sixt"},
            "location": {"city": "Berlin", "country": "de", "remote": False}}]},
    }

    def fetch(self, provider):
        payload = self.PAYLOADS[provider]
        with patch.object(jobsearch, "_get_json", lambda url: payload):
            return list(jobsearch._ATS_FETCHERS[provider]("slug", "Co"))

    def test_greenhouse(self):
        [j] = self.fetch("greenhouse")
        self.assertEqual((j["company"], j["location"]),
                         ("Monzo", "London, United Kingdom"))
        self.assertEqual(j["description"], "React & TypeScript")
        self.assertEqual(j["tags"], ["Engineering"])

    def test_lever(self):
        [j] = self.fetch("lever")
        self.assertEqual(j["url"], "https://jobs.lever.co/palantir/1")
        self.assertFalse(j["remote"])

    def test_ashby_skips_unlisted_and_joins_locations(self):
        [j] = self.fetch("ashby")
        self.assertEqual(j["location"], "Berlin, Remote - Germany")
        self.assertTrue(j["remote"])

    def test_workable_and_smartrecruiters(self):
        [w] = self.fetch("workable")
        self.assertEqual((w["company"], w["location"]), ("Acme", "Berlin, Germany"))
        [s] = self.fetch("smartrecruiters")
        self.assertEqual(s["url"], "https://jobs.smartrecruiters.com/slug/77")
        self.assertEqual(s["location"], "Berlin, DE")

    def test_one_failing_feed_is_skipped(self):
        feeds = [{"provider": "greenhouse", "slug": "bad"},
                 {"provider": "greenhouse", "slug": "good"}]

        def fake(url):
            if "/bad/" in url:
                raise OSError("404")
            return self.PAYLOADS["greenhouse"]
        with patch.object(jobsearch.config, "sources", lambda: {"ats": feeds}), \
                patch.object(jobsearch, "_get_json", fake):
            rows = list(jobsearch._fetch_ats())
        self.assertEqual([r["_source"] for r in rows], ["greenhouse:good"])

    def test_all_feeds_failing_raises(self):
        feeds = [{"provider": "lever", "slug": "x"}]

        def boom(url):
            raise OSError("down")
        with patch.object(jobsearch.config, "sources", lambda: {"ats": feeds}), \
                patch.object(jobsearch, "_get_json", boom):
            with self.assertRaises(RuntimeError):
                list(jobsearch._fetch_ats())

    def test_config_rejects_unknown_provider(self):
        data = json.loads((ROOT / "config.json").read_text())
        data["sources"]["ats"] = [{"provider": "nope", "slug": "x"}]
        with self.assertRaisesRegex(ValueError, "provider"):
            config.validate(data)
