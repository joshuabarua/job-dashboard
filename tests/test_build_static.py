import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_static as bs

TEMPLATE = '<script>const U = "__SUPABASE_URL__"; const K = "__SUPABASE_KEY__";</script>'

GOOD_ENV = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_KEY_PUBLIC": "sb_publishable_testkey",
    "SUPABASE_SERVICE_KEY": "service-role-key-value",
}

BAD_ENVS = {
    "missing both": ({}, ["SUPABASE_URL", "SUPABASE_KEY_PUBLIC"]),
    "missing url": ({"SUPABASE_KEY_PUBLIC": "sb_publishable_x"}, ["SUPABASE_URL"]),
    "missing public key": ({"SUPABASE_URL": "https://x.supabase.co"}, ["SUPABASE_KEY_PUBLIC"]),
    "whitespace url": (
        {"SUPABASE_URL": "   ", "SUPABASE_KEY_PUBLIC": "sb_publishable_x"},
        ["SUPABASE_URL"],
    ),
    "whitespace public key": (
        {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY_PUBLIC": "  \t "},
        ["SUPABASE_KEY_PUBLIC"],
    ),
}


class BuildStaticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        static = self.base / "app" / "static"
        static.mkdir(parents=True)
        (static / "index.html").write_text(TEMPLATE, encoding="utf-8")
        (static / "style.css").write_text("css", encoding="utf-8")
        (static / "favicon.svg").write_text("svg", encoding="utf-8")
        self.public = self.base / "public"
        self.public.mkdir()
        for name in ("index.html", "jobs.json", "stats.json", "meta.json"):
            (self.public / name).write_text(f"sentinel-{name}", encoding="utf-8")

    def make_tracker(self):
        tracker = mock.MagicMock()
        tracker.get_jobs.return_value = []
        tracker.dedup_status.side_effect = lambda jobs: jobs
        tracker.sort_jobs.side_effect = lambda jobs: jobs
        tracker.pipeline.return_value = []
        tracker.locations.return_value = []
        tracker.TRACKS = []
        tracker.WORKFLOW = ["New", "Applied"]
        return tracker

    def sentinels(self):
        return {p.name: p.read_text(encoding="utf-8") for p in self.public.iterdir()}

    def run_build(self, env, tracker):
        with mock.patch.object(bs, "BASE", self.base), \
             mock.patch.object(bs, "PUBLIC", self.public), \
             mock.patch.object(bs, "tracker", tracker), \
             mock.patch.dict(os.environ, env, clear=True):
            return bs.build()

    def test_bad_env_raises_before_any_writes_or_tracker_calls(self):
        for label, (env, names) in BAD_ENVS.items():
            with self.subTest(label=label):
                tracker = self.make_tracker()
                before = self.sentinels()
                with self.assertRaises(ValueError) as ctx:
                    self.run_build(env, tracker)
                for name in names:
                    self.assertIn(name, str(ctx.exception))
                tracker.get_jobs.assert_not_called()
                self.assertEqual(self.sentinels(), before)

    def test_service_key_as_public_key_raises(self):
        env = dict(GOOD_ENV, SUPABASE_KEY_PUBLIC=GOOD_ENV["SUPABASE_SERVICE_KEY"])
        tracker = self.make_tracker()
        before = self.sentinels()
        with self.assertRaises(ValueError) as ctx:
            self.run_build(env, tracker)
        self.assertIn("SUPABASE_KEY_PUBLIC", str(ctx.exception))
        tracker.get_jobs.assert_not_called()
        self.assertEqual(self.sentinels(), before)

    def test_sb_secret_key_as_public_key_raises(self):
        env = dict(GOOD_ENV, SUPABASE_KEY_PUBLIC="sb_secret_embedded")
        tracker = self.make_tracker()
        before = self.sentinels()
        with self.assertRaises(ValueError):
            self.run_build(env, tracker)
        tracker.get_jobs.assert_not_called()
        self.assertEqual(self.sentinels(), before)

    def test_render_static_html_injects_url_and_public_key_only(self):
        with mock.patch.object(bs, "BASE", self.base), \
             mock.patch.dict(os.environ, GOOD_ENV, clear=True):
            html = bs.render_static_html()
        self.assertIn(GOOD_ENV["SUPABASE_URL"], html)
        self.assertIn(GOOD_ENV["SUPABASE_KEY_PUBLIC"], html)
        self.assertNotIn(GOOD_ENV["SUPABASE_SERVICE_KEY"], html)
        self.assertNotIn("__SUPABASE_URL__", html)
        self.assertNotIn("__SUPABASE_KEY__", html)

    def test_build_writes_injected_site(self):
        tracker = self.make_tracker()
        self.run_build(dict(GOOD_ENV), tracker)
        tracker.get_jobs.assert_called_once_with(strict=True)
        html = (self.public / "index.html").read_text(encoding="utf-8")
        self.assertIn(GOOD_ENV["SUPABASE_KEY_PUBLIC"], html)
        self.assertIn(GOOD_ENV["SUPABASE_URL"], html)
        self.assertNotIn(GOOD_ENV["SUPABASE_SERVICE_KEY"], html)
        self.assertEqual(json.loads((self.public / "jobs.json").read_text()), [])
        stats = json.loads((self.public / "stats.json").read_text())
        self.assertEqual(stats["total"], 0)
        meta = json.loads((self.public / "meta.json").read_text())
        self.assertEqual(meta["workflow"], ["New", "Applied"])
        self.assertTrue((self.public / "style.css").exists())
        self.assertTrue((self.public / "favicon.svg").exists())


if __name__ == "__main__":
    unittest.main()
