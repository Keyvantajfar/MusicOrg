"""Duplicate detection.

Fixes over the old script:
* files with missing tags are NEVER grouped by accident - they go to a Review list
* artist is part of the key (configurable), duration must agree (configurable)
* keeper chosen by an ordered, user-defined criteria list with a tie tolerance
* optional exact (byte-identical) detection
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .config import Config
from .models import Track
from .plan import Action, Plan
from .util import normalize_text

NUMERIC_TOL = {"bit_depth", "sample_rate", "bitrate", "size"}
ALL_CRITERIA = ["lossless", "bit_depth", "sample_rate", "bitrate", "has_picture", "pic_resolution",
                "tag_completeness", "size", "newest", "oldest", "preferred_path"]


@dataclass
class DupGroup:
    key: str
    members: list[Track]
    keeper: Track
    reasons: list[str] = field(default_factory=list)
    exact: bool = False

    @property
    def losers(self) -> list[Track]:
        return [m for m in self.members if m is not self.keeper]


@dataclass
class DedupeResult:
    groups: list[DupGroup] = field(default_factory=list)
    review: list[tuple[Track, str]] = field(default_factory=list)   # (track, why)
    unique: int = 0

    @property
    def loser_count(self) -> int:
        return sum(len(g.losers) for g in self.groups)

    @property
    def loser_bytes(self) -> int:
        return sum(t.size for g in self.groups for t in g.losers)


# --------------------------------------------------------------------- keys
def _is_missing(v: str, cfg: Config) -> bool:
    return (v or "").strip().casefold() in {u.casefold() for u in cfg.dedupe.unknown_values}


def _stem_title(t: Track) -> str:
    stem = os.path.splitext(t.name)[0]
    return re.sub(r"^\s*\d{1,3}\s*[-._)]\s*", "", stem).strip()


def _key(t: Track, cfg: Config) -> tuple[tuple | None, str]:
    """(key, reason_if_unusable)"""
    d = cfg.dedupe
    parts, missing = [], []
    for f in d.match_fields:
        v = getattr(t, f, "") or ""
        if f == "title" and _is_missing(v, cfg) and d.missing_policy == "filename":
            v = _stem_title(t)
        if _is_missing(v, cfg):
            if d.missing_policy == "loose":
                v = ""
            else:
                missing.append(f)
                continue
        parts.append(normalize_text(v, case=d.case_insensitive, punct=d.strip_punctuation, brackets=d.strip_brackets))
    if missing:
        return None, "missing " + ", ".join(missing)
    return tuple(parts), ""


def _file_hash(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


# ------------------------------------------------------------------ ranking
def _value(t: Track, crit: str, cfg: Config) -> float:
    if crit == "lossless":
        return 1.0 if t.lossless else 0.0
    if crit == "has_picture":
        return 1.0 if t.has_picture else 0.0
    if crit == "pic_resolution":
        return float(t.pic_res)
    if crit == "tag_completeness":
        return float(sum(bool(x) for x in (t.title, t.artist, t.album, t.albumartist, t.track, t.year, t.genre)))
    if crit == "newest":
        return float(t.mtime_ns)
    if crit == "oldest":
        return -float(t.mtime_ns)
    if crit == "preferred_path":
        p = t.path.casefold()
        return 1.0 if any(s.casefold() in p for s in cfg.dedupe.preferred_paths) else 0.0
    if crit == "size":
        return float(t.size)
    return float(getattr(t, crit, 0) or 0)


def choose_keeper(members: list[Track], cfg: Config) -> tuple[Track, list[str]]:
    cands, reasons = list(members), []
    tol = cfg.dedupe.tie_tolerance_pct / 100.0
    for crit in cfg.dedupe.criteria:
        if len(cands) == 1:
            break
        if crit not in ALL_CRITERIA:
            continue
        vals = {id(t): _value(t, crit, cfg) for t in cands}
        best = max(vals.values())
        floor = best * (1 - tol) if crit in NUMERIC_TOL and best > 0 else best
        kept = [t for t in cands if vals[id(t)] >= floor]
        if len(kept) < len(cands):
            lost = [vals[id(t)] for t in cands if t not in kept]
            reasons.append(f"{crit}: {best:g} beats {max(lost):g}")
        cands = kept
    cands.sort(key=lambda t: t.path)
    if len(cands) > 1:
        reasons.append("tie on all criteria -> first path")
    return cands[0], reasons


# --------------------------------------------------------------------- main
def find_duplicates(tracks: list[Track], cfg: Config, progress=None) -> DedupeResult:
    d, n = cfg.dedupe, len(tracks)
    uf = _UF(n)
    keys: list[tuple | None] = []
    why: list[str] = []
    for t in tracks:
        k, w = _key(t, cfg)
        keys.append(k)
        why.append(w)

    # 1. tag key groups, split by duration clusters
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        if k is not None:
            buckets[k].append(i)
    for idxs in buckets.values():
        if len(idxs) < 2:
            continue
        idxs.sort(key=lambda i: tracks[i].duration)
        for a, b in zip(idxs, idxs[1:]):
            if d.duration_tolerance_s <= 0 or tracks[b].duration - tracks[a].duration <= d.duration_tolerance_s:
                uf.union(a, b)

    # 2. byte-identical files
    exact_roots: set[int] = set()
    if d.exact_hash:
        by_size: dict[int, list[int]] = defaultdict(list)
        for i, t in enumerate(tracks):
            by_size[t.size].append(i)
        todo = [i for v in by_size.values() if len(v) > 1 for i in v]
        by_hash: dict[str, list[int]] = defaultdict(list)
        for c, i in enumerate(todo):
            try:
                by_hash[_file_hash(tracks[i].path)].append(i)
            except OSError:
                pass
            if progress and c % 50 == 0:
                progress(c, len(todo), "hashing")
        for idxs in by_hash.values():
            for a, b in zip(idxs, idxs[1:]):
                uf.union(a, b)
            if len(idxs) > 1:
                exact_roots.update(idxs)

    comps: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        comps[uf.find(i)].append(i)

    res = DedupeResult()
    for idxs in comps.values():
        members = [tracks[i] for i in idxs]
        if len(members) == 1:
            i = idxs[0]
            if keys[i] is None:
                res.review.append((tracks[i], why[i]))
            else:
                res.unique += 1
            continue
        keeper, reasons = choose_keeper(members, cfg)
        k = next((keys[i] for i in idxs if keys[i] is not None), None)
        res.groups.append(DupGroup(" | ".join(k) if k else "(identical bytes)", members, keeper, reasons,
                                   exact=any(i in exact_roots for i in idxs)))
    res.groups.sort(key=lambda g: g.key)
    res.review.sort(key=lambda r: r[0].path)
    return res


def build_plan(result: DedupeResult, cfg: Config) -> Plan:
    d = cfg.dedupe
    plan = Plan("dedupe", meta={"action": d.action, "groups": len(result.groups)})
    qdir = str(cfg.sub_dir("quarantine", d.quarantine_dir))
    for g in result.groups:
        for t in g.losers:
            note = f"duplicate of {g.keeper.name}"
            if d.action == "bin":
                plan.actions.append(Action("trash", src=t.path, note=note))
            elif d.action == "delete":
                plan.actions.append(Action("delete", src=t.path, note=note))
            elif d.action == "quarantine":
                plan.actions.append(Action("move", src=t.path, dst=_unique(os.path.join(qdir, t.name)), note=note))
    return plan


def review_dir_plan(items: list[Track], cfg: Config) -> Plan:
    rdir = str(cfg.sub_dir("review", cfg.dedupe.review_dir))
    plan = Plan("dedupe", meta={"review": True})
    for t in items:
        plan.actions.append(Action("move", src=t.path, dst=_unique(os.path.join(rdir, t.name)), note="needs labelling"))
    return plan


def _unique(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{stem} ({i}){ext}"):
        i += 1
    return f"{stem} ({i}){ext}"
