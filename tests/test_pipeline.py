import json
import os
import sys
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
    def assertRejected(self, j):
        self.assertIsNotNone(jobsearch._rejected(j), j["job_title"])

    def assertNotRejected(self, j):
        self.assertIsNone(jobsearch._rejected(j), j["job_title"])

    def test_skill_rejects(self):
        for title in ["Java Developer", "Golang Engineer", "Go Developer",
                      "C++ Engineer", "C# Developer", ".NET Developer"]:
            self.assertRejected(job(title=title))

    def test_skill_lookalikes_pass(self):
        for title in ["JavaScript Developer", "React Developer",
                      "Typescript Frontend Engineer"]:
            self.assertNotRejected(job(title=title))

    def test_seniority_boundary_rejects(self):
        for title in ["Sr. Frontend Developer", "SR Frontend Developer",
                      "Senior Frontend Developer"]:
            self.assertRejected(job(title=title))

    def test_boundary_lookalikes_pass(self):
        for title in ["SRE Engineer", "C10 Developer"]:
            self.assertNotRejected(job(title=title))

    def test_rejected_host(self):
        self.assertRejected(job(
            title="Frontend Engineer",
            url="https://www.ziprecruiter.com/jobs/frontend-engineer-1"))

    def test_place_text_rejects(self):
        for title in ["Boulder Valley School District Teacher",
                      "BVSD Para Educator", "City of Boulder Colorado Staff",
                      "Columbus Climbing Instructor", "Ohio Gym Manager"]:
            self.assertRejected(job(title=title))

    def test_bouldering_berlin_passes(self):
        for title in ["Boulderwelt Berlin Route Setter",
                      "Bouldering Coach Berlin"]:
            self.assertNotRejected(job(title=title))

    def test_question_and_seo_titles(self):
        for title in ["What is a Frontend Engineer",
                      "How to become a developer",
                      "Frontend Developer Jobs in Berlin",
                      "Software Engineer jobs"]:
            self.assertRejected(job(title=title))


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
            len(config.search_targets(cfg["remote"]))
            for cfg in jobsearch.TRACKS.values())
        self.assertEqual(len(queries), expected)
        for q in queries:
            self.assertTrue(any(t in q for t in config.search_targets(True)),
                            f"no geo target in query {q!r}")

        by_title = {}
        for j in jobs:
            by_title.setdefault(j["job_title"], j)

        columbus = by_title["Climbing Instructor Columbus Ohio"]
        self.assertEqual(columbus["location"], "")
        self.assertFalse(columbus["remote"])
        for cfg in jobsearch.TRACKS.values():
            self.assertFalse(jobsearch._location_ok(columbus, cfg))

        berlin = by_title["Frontend Engineer Berlin"]
        self.assertEqual(berlin["location"], "Berlin")
        self.assertFalse(berlin["remote"])

        germany = by_title["Remote Frontend Engineer Germany"]
        self.assertEqual(germany["location"], "Germany")
        self.assertTrue(germany["remote"])

        self.assertTrue(all(j["_source"] == "websearch" for j in jobs))


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
        self.assertEqual(result.report.rejected, {"Location": 1})
        self.assertEqual(result.report.errors, {})
        rendered = json.loads(result.report.render())
        self.assertEqual(set(rendered),
                         {"fetched", "accepted", "rejected", "errors"})
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
        self.assertEqual(result.report.rejected["Missing URL"], 1)
        self.assertEqual(result.report.rejected["Duplicate URL"], 1)

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


class TestDbFetchAll(unittest.TestCase):
    def test_fetch_error_raises(self):
        class _Boom:
            def table(self, name):
                raise ConnectionError("db down")

        with patch.object(db, "ENABLED", True), \
                patch.object(db, "client", _Boom()):
            with self.assertRaisesRegex(RuntimeError, "fetch_all failed"):
                db.fetch_all()

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
        with patch.object(jobsearch, "collect_with_report",
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

        with patch.object(jobsearch, "collect_with_report",
                          lambda track=None: result), \
                patch.object(tracker, "add_job", fake_add):
            run_search.run()
        self.assertEqual(added, [False])


class TestBuildStatic(unittest.TestCase):
    def test_strict_read(self):
        import build_static

        def boom(strict=False):
            raise RuntimeError("Supabase not configured")

        with patch.object(build_static.tracker, "get_jobs", boom):
            with self.assertRaises(RuntimeError):
                build_static.build()


if __name__ == "__main__":
    unittest.main()
