"""Quick check of API response."""
import urllib.request, json

d = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/jobs").read())
jobs = d if isinstance(d, list) else d.get("jobs", [])
print(f"Total: {len(jobs)} jobs\n")
for j in jobs:
    print(f"  {j['job_title'][:50]:50s} | {j.get('status',''):8s} | {j.get('date','')}")
