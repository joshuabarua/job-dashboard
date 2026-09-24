import json
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "config.json"

_data = None


def load():
    global _data
    if _data is None:
        _data = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else {}
    return _data


def tracks():
    return load().get("tracks") or {}


def cities():
    return load().get("locations", {}).get("cities") or []


def cities_lower():
    return [c.lower() for c in cities()]


def _word_pattern(words):
    words = [w for w in words if w]
    if not words:
        return None
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


def allowed_geo_pattern():
    locs = load().get("locations", {})
    return _word_pattern(cities_lower() + (locs.get("allowed_regions") or []))


def reject(key):
    return load().get("reject", {}).get(key) or []


def sources():
    return load().get("sources") or {}


def source_enabled(key):
    return bool(sources().get(key, True))


def skill_keywords():
    return set(load().get("skill_keywords") or [])
