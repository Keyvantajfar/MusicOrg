"""Build an organize plan: Artist/Album folders from a template.

Fixes over the old script:
* albums are no longer keyed by album name alone ("Greatest Hits" by two artists stay apart,
  big multi-artist albums become compilations)
* every name is sanitised (old main script crashed / mangled on '/' etc.)
* case variants of the same album/artist go into ONE folder
* files without usable tags go to an unsorted folder instead of "Unknown"
* collisions are handled by policy, nothing is silently overwritten
"""
from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .config import Config
from .models import Track
from .plan import Action, Plan
from .util import normalize_text, sanitize_component

_TOKEN = re.compile(r"\{(\w+)(?::([^}]*))?\}")


@dataclass
class OrganizeReport:
    total: int = 0
    in_place: int = 0
    unsorted: int = 0
    skipped: int = 0
    collisions: int = 0
    compilations: int = 0
    split_albums: list[str] = field(default_factory=list)


def _missing(v: str, cfg: Config) -> bool:
    return (v or "").strip().casefold() in {u.casefold() for u in cfg.dedupe.unknown_values}


def _san(s: str, cfg: Config, keep_ext: bool = False) -> str:
    o = cfg.organize
    return sanitize_component(s, profile=o.sanitize_profile, repl=o.replace_char, norm=o.unicode_norm,
                              case_mode=o.case_mode, max_len=o.max_component_len, keep_ext=keep_ext)


# ------------------------------------------------------------- album owners
def resolve_owners(tracks: list[Track], cfg: Config) -> tuple[dict[str, tuple[str, str]], OrganizeReport]:
    """path -> (albumartist_folder, album_folder)"""
    o = cfg.organize
    rep = OrganizeReport()
    out: dict[str, tuple[str, str]] = {}

    def album_name(t):
        return t.album if not _missing(t.album, cfg) else ""

    # most common spelling per normalised name
    def spelling(values: list[str]) -> dict[str, str]:
        by: dict[str, Counter] = defaultdict(Counter)
        for v in values:
            by[normalize_text(v)][v] += 1
        return {k: c.most_common(1)[0][0] for k, c in by.items()}

    art_sp = spelling([x for t in tracks for x in (t.artist, t.albumartist) if x]) if o.unify_case_variants else {}
    alb_sp = spelling([t.album for t in tracks if t.album]) if o.unify_case_variants else {}

    def A(name: str) -> str:
        return art_sp.get(normalize_text(name), name) if name else name

    def B(name: str) -> str:
        return alb_sp.get(normalize_text(name), name) if name else name

    by_album: dict[str, list[Track]] = defaultdict(list)
    for t in tracks:
        by_album[normalize_text(album_name(t))].append(t)

    for akey, group in by_album.items():
        if not akey:                                  # no album tag: owner is each track's own artist
            for t in group:
                out[t.path] = (A(t.albumartist or t.artist), "")
            continue
        display = B(next(album_name(t) for t in group))
        if o.main_artist_rule == "albumartist_tag":
            for t in group:
                out[t.path] = (A(t.albumartist or t.artist), display)
            continue
        if o.main_artist_rule == "most_frequent":      # old behaviour
            cnt = Counter(A(t.artist) for t in group if t.artist)
            main = cnt.most_common(1)[0][0] if cnt else ""
            for t in group:
                out[t.path] = (main, display)
            continue
        # ---- smart
        tagged = [t for t in group if t.albumartist and not _missing(t.albumartist, cfg)]
        untagged = [t for t in group if t not in tagged]
        owners = {normalize_text(t.albumartist): A(t.albumartist) for t in tagged}
        for t in tagged:
            out[t.path] = (A(t.albumartist), display)
        rest = []
        for t in untagged:
            if normalize_text(t.artist) in owners:     # artist matches an existing album artist
                out[t.path] = (owners[normalize_text(t.artist)], display)
            else:
                rest.append(t)
        if not rest:
            continue
        cnt = Counter(normalize_text(t.artist) for t in rest if t.artist)
        distinct = len(cnt)
        top, top_n = (cnt.most_common(1)[0] if cnt else ("", 0))
        share = top_n / len(rest) if rest else 0
        if owners and len(owners) == 1 and not distinct > 1:
            who = next(iter(owners.values()))
            for t in rest:
                out[t.path] = (who, display)
        elif distinct <= 1 or share >= o.dominant_share:
            main = A(next((t.artist for t in rest if normalize_text(t.artist) == top), ""))
            for t in rest:
                out[t.path] = (main, display)
        elif distinct >= o.compilation_min_artists:
            rep.compilations += 1
            for t in rest:
                out[t.path] = (o.compilation_name, display)
        else:
            rep.split_albums.append(display)
            for t in rest:
                out[t.path] = (A(t.artist), display)
    return out, rep


# ------------------------------------------------------------- templating
def _render(template_part: str, values: dict) -> str:
    def sub(m):
        name, spec = m.group(1), m.group(2)
        v = values.get(name, "")
        if v in (None, 0, ""):
            return ""
        if spec:
            try:
                return format(v, spec)
            except (ValueError, TypeError):
                return str(v)
        return str(v)
    return _TOKEN.sub(sub, template_part)


def dest_for(t: Track, owner: str, album: str, disc_prefix: str, cfg: Config) -> str:
    """Relative destination path (with extension)."""
    o = cfg.organize
    stem, ext = os.path.splitext(t.name)
    values = {"albumartist": owner or o.unknown_artist, "artist": t.artist or o.unknown_artist,
              "album": album or o.unknown_album, "title": t.title or stem, "track": t.track or 0,
              "disc": t.disc or 0, "disc_prefix": disc_prefix, "year": t.year, "genre": t.genre,
              "filename": stem, "format": t.fmt.upper()}
    parts = [p for p in o.template.replace("\\", "/").split("/") if p]
    out = []
    for i, part in enumerate(parts):
        s = _render(part, values).strip()
        if not s:
            continue
        last = i == len(parts) - 1
        out.append(_san(s + ext if last else s, cfg, keep_ext=last))
    if not out or not parts or not _render(parts[-1], values).strip():
        out.append(_san(t.name, cfg, keep_ext=True))
    elif not out[-1].lower().endswith(ext.lower()):
        out[-1] = _san(out[-1] + ext, cfg, keep_ext=True)
    rel = "/".join(out)
    if o.max_path_len and len(rel) > o.max_path_len:    # shorten file stem
        over = len(rel) - o.max_path_len
        fn = out[-1]
        s2, e2 = os.path.splitext(fn)
        out[-1] = s2[: max(1, len(s2) - over)].rstrip() + e2
        rel = "/".join(out)
    return rel


def _free_name(path: str, taken) -> str:
    stem, ext = os.path.splitext(path)
    i = 2
    while taken(f"{stem} ({i}){ext}"):
        i += 1
    return f"{stem} ({i}){ext}"


def build_plan(tracks: list[Track], cfg: Config, dest_root: str | None = None) -> tuple[Plan, OrganizeReport]:
    o = cfg.organize
    root = os.path.expanduser(dest_root or o.dest_root)
    if not root:
        raise ValueError("Set organize.dest_root first")
    owners, rep = resolve_owners(tracks, cfg)
    rep.total = len(tracks)

    multidisc: dict[str, bool] = defaultdict(bool)
    for t in tracks:
        if t.disc and t.disc > 1:
            multidisc[normalize_text(t.album)] = True

    plan = Plan("organize", meta={"mode": o.mode, "dest_root": root,
                                  "cleanup_roots": list(cfg.scan.roots)})
    ci = o.sanitize_profile in ("windows", "ascii")
    claimed: dict[str, Action] = {}
    unsorted_root = o.unsorted_dir if os.path.isabs(o.unsorted_dir) else os.path.join(root, o.unsorted_dir)

    def key(p):
        return p.casefold() if ci else p

    def taken(p):
        return key(p) in claimed or os.path.lexists(p)

    for t in tracks:
        missing = [f for f in o.required_tags if _missing(getattr(t, f, ""), cfg)]
        owner, album = owners.get(t.path, ("", ""))
        if missing and o.unknown_policy == "skip":
            rep.skipped += 1
            continue
        if missing and o.unknown_policy == "unsorted_dir":
            dst = os.path.join(unsorted_root, _san(t.name, cfg, keep_ext=True))
            rep.unsorted += 1
            note = "unknown: missing " + ", ".join(missing)
        else:
            dp = "" if not multidisc[normalize_text(t.album)] else (f"{t.disc or 1}-")
            dst = os.path.join(root, *dest_for(t, owner, album, dp, cfg).split("/"))
            note = ""
        if os.path.abspath(dst) == os.path.abspath(t.path):
            rep.in_place += 1
            continue
        params: dict = {}
        k = key(dst)
        exists_other = os.path.lexists(dst) and not _same(t.path, dst)
        if k in claimed or exists_other:
            rep.collisions += 1
            policy = o.collision
            if policy == "skip":
                rep.skipped += 1
                continue
            if policy == "rename":
                dst = _free_name(dst, taken)
                note = (note + "; " if note else "") + "renamed (collision)"
            elif policy == "overwrite":
                params["overwrite"] = True
                note = (note + "; " if note else "") + "overwrites existing"
                if k in claimed:
                    claimed[k].enabled = False
            elif policy == "keep_larger":
                other_size = (os.path.getsize(claimed[k].src) if k in claimed else os.path.getsize(dst))
                if t.size > other_size:
                    params["overwrite"] = True
                    if k in claimed:
                        claimed[k].enabled = False
                    note = (note + "; " if note else "") + "replaces smaller file"
                else:
                    rep.skipped += 1
                    continue
        act = Action(o.mode, src=t.path, dst=dst, params=params, note=note)
        claimed[key(dst)] = act
        plan.actions.append(act)
        if o.move_sidecars:
            sb = os.path.splitext(t.path)[0]
            for ext in o.sidecar_exts:
                side = f"{sb}.{ext}"
                if os.path.exists(side):
                    sd = os.path.splitext(dst)[0] + f".{ext}"
                    plan.actions.append(Action(o.mode, src=side, dst=sd, note="sidecar"))
    if o.mode == "move" and o.remove_empty_dirs and cfg.scan.roots:
        plan.actions.append(Action("cleanup", params={"roots": [os.path.expanduser(r) for r in cfg.scan.roots]},
                                   note="remove empty folders"))
    return plan, rep


def _same(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False
