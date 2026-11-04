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
        if key == "language_level":
            path = "config.reject.language_level"
            _require(isinstance(val, dict), f"{path}: must be an object")
            _require(isinstance(val.get("max_level"), str) and
                     val["max_level"].upper() in
                     ("A1", "A2", "B1", "B2", "C1", "C2"),
                     f"{path}.max_level: must be one of A1, A2, B1, B2, "
                     "C1, C2")
            for field in ("target_terms", "other_terms",
                          "optional_markers"):
                _require(_nonempty_str_list(val.get(field)),
                         f"{path}.{field}: must be a non-empty list "
                         "of strings")
            _require(isinstance(val.get("max_distance"), int)
                     and not isinstance(val.get("max_distance"), bool)
                     and val["max_distance"] > 0,
                     f"{path}.max_distance: must be a positive integer")
            continue
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

    resume = data.get("resume")
    if resume is not None:
        _require(isinstance(resume, dict), "config.resume: must be an object")
        _require(isinstance(resume.get("min_detected"), int)
                 and not isinstance(resume.get("min_detected"), bool)
                 and resume["min_detected"] > 0,
                 "config.resume.min_detected: must be a positive integer")
        ratio = resume.get("min_ratio")
        _require(isinstance(ratio, (int, float))
                 and not isinstance(ratio, bool) and 0 <= ratio <= 1,
                 "config.resume.min_ratio: must be a number in [0, 1]")
        groups = resume.get("skill_groups")
        _require(isinstance(groups, dict),
                 "config.resume.skill_groups: must be an object")
        for name, entries in groups.items():
            path = f"config.resume.skill_groups.{name}"
            _require(isinstance(entries, list) and entries,
                     f"{path}: must be a non-empty list")
            for i, aliases in enumerate(entries):
                _require(_nonempty_str_list(aliases),
                         f"{path}[{i}]: must be a non-empty list of strings")
        cvs = resume.get("cvs")
        _require(isinstance(cvs, dict) and cvs,
                 "config.resume.cvs: must be a non-empty object")
        for cv_label, cv in cvs.items():
            path = f"config.resume.cvs.{cv_label}"
            _require(isinstance(cv, dict), f"{path}: must be an object")
            for field in ("have", "gaps"):
                _require(_nonempty_str_list(cv.get(field)),
                         f"{path}.{field}: must be a non-empty list "
                         "of strings")
                for grp in cv[field]:
                    _require(grp in groups,
                             f"{path}.{field}: unknown skill group {grp!r}")
        for name, cfg in tracks.items():
            _require(cfg["cv"] in cvs,
                     f"config.tracks.{name}.cv: must be a key in resume.cvs "
                     f"(got {cfg['cv']!r})")
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


def language_level():
    return load().get("reject", {}).get("language_level") or {}


def sources():
    return load().get("sources") or {}


def source_enabled(key):
    return bool(sources().get(key, True))


def resume():
    return load().get("resume") or {}


def skill_keywords():
    return set(load().get("skill_keywords") or [])
