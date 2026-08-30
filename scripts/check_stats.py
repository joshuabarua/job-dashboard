"""Quick stats check."""
import urllib.request, json

d = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/stats").read())
print(f"Total: {d['total']}")
print("\nPipeline:")
for p in d["pipeline"]:
    print(f"  {p['status']}: {p['count']}")
print("\nTracks (non-zero):")
for t in d["tracks"]:
    if t["count"] > 0:
        print(f"  {t['track']}: {t['count']}")
