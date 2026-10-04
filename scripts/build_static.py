"""Build static JSON + assets for GitHub Pages."""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import tracker
from app.main import TRACK_COLORS

BASE = Path(__file__).resolve().parent.parent
PUBLIC = BASE / "public"

def _job_payload(job, key):
    j = dict(job)
    j["_key"] = key
    j["_track_color"] = TRACK_COLORS.get(job.get("track", ""), "accent")
    return j

def render_static_html():
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_KEY_PUBLIC", "").strip()
    missing = [
        name
        for name, value in (("SUPABASE_URL", supabase_url), ("SUPABASE_KEY_PUBLIC", supabase_key))
        if not value
    ]
    if missing:
        raise ValueError(f"missing required env vars for static build: {', '.join(missing)}")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if supabase_key.startswith("sb_secret_") or (service_key and supabase_key == service_key):
        raise ValueError("SUPABASE_KEY_PUBLIC appears to be a secret key; refusing to embed it in the static site")
    html = (BASE / "app" / "static" / "index.html").read_text(encoding="utf-8")
    return html.replace("__SUPABASE_URL__", supabase_url).replace("__SUPABASE_KEY__", supabase_key)

def build():
    html = render_static_html()

    PUBLIC.mkdir(exist_ok=True)

    jobs = tracker.get_jobs(strict=True)
    jobs = tracker.dedup_status(jobs)
    jobs = tracker.sort_jobs(jobs)

    meta = {
        "tracks": tracker.TRACKS,
        "workflow": tracker.WORKFLOW,
        "track_colors": TRACK_COLORS,
    }

    stats = {
        "total": len(jobs),
        "pipeline": tracker.pipeline(jobs),
        "tracks": [
            {"track": t, "count": sum(1 for j in jobs if j.get("track", "").strip() == t)}
            for t in tracker.TRACKS
        ],
        "locations": [{"location": l, "count": c} for l, c in tracker.locations(jobs)],
    }

    payload = [_job_payload(j, i) for i, j in enumerate(jobs)]

    (PUBLIC / "jobs.json").write_text(json.dumps(payload), encoding="utf-8")
    (PUBLIC / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    (PUBLIC / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    (PUBLIC / "index.html").write_text(html, encoding="utf-8")

    shutil.copy2(BASE / "app" / "static" / "style.css", PUBLIC / "style.css")
    shutil.copy2(BASE / "app" / "static" / "favicon.svg", PUBLIC / "favicon.svg")

    print(f"Built static site: {len(jobs)} jobs, {len(stats['locations'])} locations")

if __name__ == "__main__":
    build()
