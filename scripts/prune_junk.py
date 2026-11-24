"""Re-check tracker rows against today's hygiene filters and remove junk.

Filters improve over time; rows added before a filter existed stay on the
board. This re-runs the track-independent hygiene stage (listing pages,
content pages, rejected hosts) over existing rows.

Only rows still in status "New" are considered — anything you have touched
(Applied, Starred, Declined, ...) is never removed.

    python scripts/prune_junk.py            # dry run: list what would go
    python scripts/prune_junk.py --apply    # delete them from Supabase
    python scripts/prune_junk.py --json public/jobs.json   # dry run offline
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import search, tracker


def find_junk(jobs):
    junk = []
    for job in jobs:
        if (job.get("status") or "New").strip() != "New":
            continue
        reason = search._hygiene_reject({**job, "tags": []})
        if reason:
            junk.append((job, reason))
    return junk


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="delete matching rows (default: dry run)")
    ap.add_argument("--json", type=Path,
                    help="read jobs from a jobs.json export instead of Supabase")
    args = ap.parse_args(argv)
    if args.apply and args.json:
        ap.error("--apply needs Supabase, not --json")

    if args.json:
        jobs = json.loads(args.json.read_text(encoding="utf-8"))
    else:
        jobs = tracker.get_jobs(strict=True)

    junk = find_junk(jobs)
    for job, reason in junk:
        print(f"{reason:24} | {job.get('job_title', '')[:80]} | {job.get('url', '')[:70]}")
    print(f"\n{len(junk)} of {len(jobs)} rows match: "
          f"{dict(Counter(r for _, r in junk))}")

    if not args.apply:
        print("Dry run — re-run with --apply to delete.")
        return 0
    removed = 0
    for job, _ in junk:
        ident = job.get("id") or job.get("url")
        ok, msg = tracker.remove_job(ident)
        if ok:
            removed += 1
        else:
            print(f"failed to remove {ident}: {msg}", file=sys.stderr)
    print(f"Removed {removed} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
