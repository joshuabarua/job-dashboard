"""Test UK/HTML job search and capture why nothing is found."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import traceback
from app.search import collect

try:
    cands = collect(track="Developer")
    print(f"Total candidates for Developer: {len(cands)}")
    by_source = {}
    for c in cands:
        by_source.setdefault(c["company"], 0)
        by_source[c["company"]] += 1
    for k, v in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    for c in cands[:10]:
        print(f"  - {c['job_title'][:60]} | {c['company']} | {c['location']} | score {c['match_score']}")
except Exception as e:
    traceback.print_exc()
