"""Check consolidated locations."""
import urllib.request, json

d = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/api/stats").read())
print("Locations:")
for l in d["locations"]:
    print(f"  {l['location']}: {l['count']}")
