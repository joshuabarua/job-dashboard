"""Run a full cloud search and append new candidates to jobs_tracker.csv."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("JOBS_TRACKER_CSV", str(ROOT / "jobs_tracker.csv"))

from app import search, tracker

def run():
    candidates = search.collect(track=None)
    added = 0
    skipped = 0
    for c in candidates:
        if c.get("_dup_flag"):
            skipped += 1
            continue
        ok, _ = tracker.add_job(c)
        if ok:
            added += 1
        else:
            skipped += 1
    print(f"Search complete: {added} added, {skipped} skipped (duplicates), {len(candidates)} total candidates")

if __name__ == "__main__":
    run()
