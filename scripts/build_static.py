"""Build static JSON + assets for GitHub Pages."""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JOBS_TRACKER_CSV", str(ROOT / "jobs_tracker.csv"))

from app import tracker
from app.main import TRACK_COLORS

BASE = Path(__file__).resolve().parent.parent
PUBLIC = BASE / "public"
PUBLIC.mkdir(exist_ok=True)

def _job_payload(job, key):
    j = dict(job)
    j["_key"] = key
    j["_track_color"] = TRACK_COLORS.get(job.get("track", ""), "accent")
    return j

def build():
    jobs = tracker.get_jobs()
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

    shutil.copy2(BASE / "app" / "static" / "style.css", PUBLIC / "style.css")
    shutil.copy2(BASE / "app" / "static" / "favicon.svg", PUBLIC / "favicon.svg")

    print(f"Built static site: {len(jobs)} jobs, {len(stats['locations'])} locations")

if __name__ == "__main__":
    build()
