"""Quick check of score sort."""
import urllib.request, json

d = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/jobs?sort=score").read())
jobs = d if isinstance(d, list) else d.get("jobs", [])
print(f"Total: {len(jobs)}")
for j in jobs[:5]:
    print(f"  {j['job_title'][:50]:50s} | score {j.get('match_score', '')}")
