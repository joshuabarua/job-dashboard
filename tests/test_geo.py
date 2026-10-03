import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app import search as jobsearch


def job(location="", title="", remote=False, tags=None):
    return {"job_title": title, "company": "", "location": location,
            "url": "", "tags": tags or [], "remote": remote}


class TestConfigValidation(unittest.TestCase):
    def test_loads_shipped_config(self):
        data = config.load()
        self.assertTrue(data["tracks"])
        self.assertTrue(data["locations"]["geo_groups"])

    def test_rejects_bad_config(self):
        bad = [
            {},
            {"tracks": {}},
            {"tracks": {"T": {"keywords": [], "remote": True,
                              "cv": "x", "color": "y"}},
             "locations": {"geo_groups": [
                 {"label": "G", "terms": ["g"], "onsite": True,
                  "remote": True}]}},
            {"tracks": {"T": {"keywords": ["k"], "remote": "yes",
                              "cv": "x", "color": "y"}},
             "locations": {"geo_groups": [
                 {"label": "G", "terms": ["g"], "onsite": True,
                  "remote": True}]}},
            {"tracks": {"T": {"keywords": ["k"], "remote": True,
                              "cv": "x", "color": "y"}},
             "locations": {"geo_groups": []}},
            {"tracks": {"T": {"keywords": ["k"], "remote": True,
                              "cv": "x", "color": "y"}},
             "locations": {"geo_groups": [
                 {"label": "G", "terms": ["g"], "onsite": True,
                  "remote": True},
                 {"label": "G", "terms": ["h"], "onsite": True,
                  "remote": True}]}},
            {"tracks": {"T": {"keywords": ["k"], "remote": True,
                              "cv": "x", "color": "y",
                              "search_terms": []}},
             "locations": {"geo_groups": [
                 {"label": "G", "terms": ["g"], "onsite": True,
                  "remote": True}]}},
            {"tracks": {"T": {"keywords": ["k"], "remote": True,
                              "cv": "x", "color": "y",
                              "search_terms": ["ok", ""]}},
             "locations": {"geo_groups": [
                 {"label": "G", "terms": ["g"], "onsite": True,
                  "remote": True}]}},
            {"tracks": {"T": {"keywords": ["k"], "remote": True,
                              "cv": "x", "color": "y",
                              "context_keywords": []}},
             "locations": {"geo_groups": [
                 {"label": "G", "terms": ["g"], "onsite": True,
                  "remote": True}]}},
            {"tracks": {"T": {"keywords": ["k"], "remote": True,
                              "cv": "x", "color": "y",
                              "role_keywords": ["x", 1]}},
             "locations": {"geo_groups": [
                 {"label": "G", "terms": ["g"], "onsite": True,
                  "remote": True}]}},
            {"tracks": {"T": {"keywords": ["k"], "remote": True,
                              "cv": "x", "color": "y"}},
             "locations": {"geo_groups": [
                 {"label": "G", "terms": ["g"], "onsite": True,
                  "remote": True}]},
             "sources": {"boards": [
                 {"name": "B", "url": "https://x", "remote": False}]}},
        ]
        for data in bad:
            with self.assertRaises(ValueError, msg=f"accepted: {data}"):
                config.validate(data)

    def test_search_targets(self):
        self.assertEqual(config.search_targets(True),
                         ["Berlin", "Germany", "United Kingdom"])
        self.assertEqual(config.search_targets(False),
                         ["Berlin", "United Kingdom"])


class TestDetectGeo(unittest.TestCase):
    def test_onsite(self):
        self.assertEqual(config.detect_geo("Berlin", False), "Berlin")
        self.assertEqual(config.detect_geo("Munich, Germany", False), "")
        self.assertEqual(config.detect_geo("Manchester", False),
                         "United Kingdom")
        self.assertEqual(config.detect_geo("Glasgow, Scotland", False),
                         "United Kingdom")

    def test_remote(self):
        self.assertEqual(config.detect_geo("Remote Germany", True), "Germany")
        self.assertEqual(config.detect_geo("Remote UK", True),
                         "United Kingdom")
        self.assertEqual(config.detect_geo("Remote Berlin", True), "Berlin")

    def test_remote_rejects_unscoped(self):
        for text in ["Worldwide", "Europe", "EMEA", "US", "Canada",
                     "Columbus Ohio", "Remote", ""]:
            self.assertEqual(config.detect_geo(text, True), "",
                             f"should not detect geo in {text!r}")

    def test_symbol_safe_terms(self):
        self.assertEqual(config.detect_geo("ukulele jobs", False), "")
        self.assertEqual(config.detect_geo("berlinerstrasse", False), "")
        self.assertEqual(config.detect_geo("london, u.k.", False),
                         "United Kingdom")


class TestIsRemote(unittest.TestCase):
    def test_remote_markers(self):
        for loc in ["Remote", "Remote - Germany", "Work from home",
                    "Home Office", "Homeoffice", "WFH", "Distributed"]:
            self.assertTrue(jobsearch._is_remote(job(location=loc)), loc)
        self.assertTrue(jobsearch._is_remote(
            job(location="Berlin", remote=True)))
        self.assertTrue(jobsearch._is_remote(
            job(title="Telecommute React Developer")))

    def test_not_remote(self):
        self.assertFalse(jobsearch._is_remote(job(location="Berlin")))
        self.assertFalse(jobsearch._is_remote(job(location="RemoteOK")))


class TestLocationOk(unittest.TestCase):
    onsite = {"remote": False}
    remote = {"remote": True}

    def test_onsite_matrix(self):
        for loc in ["Berlin", "London", "Manchester", "Glasgow, Scotland"]:
            self.assertTrue(
                jobsearch._location_ok(job(location=loc), self.onsite), loc)
        for loc in ["Munich, Germany", "Germany", "Hamburg", "Paris",
                    "New York", ""]:
            self.assertFalse(
                jobsearch._location_ok(job(location=loc), self.onsite), loc)

    def test_remote_matrix(self):
        for loc in ["Remote - Germany", "Remote - UK", "Remote Berlin",
                    "Berlin"]:
            self.assertTrue(
                jobsearch._location_ok(
                    job(location=loc, remote=True), self.remote), loc)
        for loc in ["Worldwide", "Europe", "EMEA", "US", "Canada",
                    "Remote", ""]:
            self.assertFalse(
                jobsearch._location_ok(
                    job(location=loc, remote=True), self.remote), loc)

    def test_track_remote_gate(self):
        remote_berlin = job(location="Remote - Berlin", remote=True)
        self.assertFalse(
            jobsearch._location_ok(remote_berlin, self.onsite))
        self.assertTrue(
            jobsearch._location_ok(remote_berlin, self.remote))


if __name__ == "__main__":
    unittest.main()
