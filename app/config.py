import json
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "config.json"

_data = None


def _require(cond, msg):
    if not cond:
        raise ValueError(msg)


def _nonempty_str(value):
    return isinstance(value, str) and bool(value.strip())


def _nonempty_str_list(value):
    return isinstance(value, list) and bool(value) and all(
        _nonempty_str(v) for v in value)


def validate(data):
    _require(isinstance(data, dict), "config: top level must be a JSON object")

    tracks = data.get("tracks")
    _require(isinstance(tracks, dict) and tracks,
             "config.tracks: must be a non-empty object")
    for name, cfg in tracks.items():
        path = f"config.tracks.{name}"
        _require(isinstance(cfg, dict), f"{path}: must be an object")
        _require(_nonempty_str_list(cfg.get("keywords")),
                 f"{path}.keywords: must be a non-empty list of strings")
        _require(isinstance(cfg.get("remote"), bool),
                 f"{path}.remote: must be a boolean")
        for field in ("search_terms", "context_keywords", "role_keywords"):
            val = cfg.get(field)
            if val is not None:
                _require(_nonempty_str_list(val),
                         f"{path}.{field}: must be a non-empty list "
                         "of strings")
        for field in ("cv", "color"):
            _require(_nonempty_str(cfg.get(field)),
                     f"{path}.{field}: must be a non-empty string")

    locations = data.get("locations") or {}
    _require(isinstance(locations, dict), "config.locations: must be an object")
    groups = locations.get("geo_groups")
    _require(isinstance(groups, list) and groups,
             "config.locations.geo_groups: must be a non-empty list")
    labels = set()
    for i, g in enumerate(groups):
        path = f"config.locations.geo_groups[{i}]"
        _require(isinstance(g, dict), f"{path}: must be an object")
        _require(_nonempty_str(g.get("label")),
                 f"{path}.label: must be a non-empty string")
        _require(g["label"] not in labels,
                 f"{path}.label: duplicate label {g['label']!r}")
        labels.add(g["label"])
        _require(_nonempty_str_list(g.get("terms")),
                 f"{path}.terms: must be a non-empty list of strings")
        for field in ("onsite", "remote"):
            _require(isinstance(g.get(field), bool),
                     f"{path}.{field}: must be a boolean")

    reject = data.get("reject") or {}
    _require(isinstance(reject, dict), "config.reject: must be an object")
    for key, val in reject.items():
        _require(isinstance(val, list),
                 f"config.reject.{key}: must be a list")

    sources = data.get("sources") or {}
    _require(isinstance(sources, dict), "config.sources: must be an object")
    for i, b in enumerate(sources.get("boards") or []):
        path = f"config.sources.boards[{i}]"
        _require(isinstance(b, dict), f"{path}: must be an object")
        for field in ("name", "url", "location"):
            _require(_nonempty_str(b.get(field)),
                     f"{path}.{field}: must be a non-empty string")
        _require(isinstance(b.get("remote"), bool),
                 f"{path}.remote: must be a boolean")
    for i, s in enumerate(sources.get("extract_seeds") or []):
        path = f"config.sources.extract_seeds[{i}]"
        _require(isinstance(s, dict), f"{path}: must be an object")
        for field in ("url", "location"):
            _require(_nonempty_str(s.get(field)),
                     f"{path}.{field}: must be a non-empty string")
        _require(isinstance(s.get("remote"), bool),
                 f"{path}.remote: must be a boolean")
    return data


def load():
    global _data
    if _data is None:
        raw = json.loads(_PATH.read_text(encoding="utf-8")) if _PATH.exists() else {}
        _data = validate(raw)
    return _data


def tracks():
    return load().get("tracks") or {}


def cities():
    return load().get("locations", {}).get("cities") or []


def cities_lower():
    return [c.lower() for c in cities()]


def geo_groups():
    return load().get("locations", {}).get("geo_groups") or []


_patterns = {}


def _term_pattern(terms):
    key = tuple(terms)
    if key not in _patterns:
        _patterns[key] = re.compile(
            "|".join(
                rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])"
                for t in terms),
            re.I)
    return _patterns[key]


def detect_geo(text, remote):
    text = (text or "").lower()
    if not text:
        return ""
    for g in geo_groups():
        if remote and not g["remote"]:
            continue
        if not remote and not g["onsite"]:
            continue
        if _term_pattern(g["terms"]).search(text):
            return g["label"]
    return ""


def search_targets(remote):
    field = "remote" if remote else "onsite"
    return [g["label"] for g in geo_groups() if g[field]]


def reject(key):
    return load().get("reject", {}).get(key) or []


def sources():
    return load().get("sources") or {}


def source_enabled(key):
    return bool(sources().get(key, True))


def skill_keywords():
    return set(load().get("skill_keywords") or [])
