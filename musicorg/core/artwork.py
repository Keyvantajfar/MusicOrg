"""Find tracks without (or with tiny) cover art and fill them from donors."""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

from .config import Config
from .dedupe import DedupeResult, find_duplicates
from .models import Track
from .plan import Action, Plan
from .tags import LOSSLESS_EXT
from .util import normalize_text

EMBED_EXTS = {"flac", "mp3", "m4a", "mp4", "wav", "aiff", "aif"}
IMG_EXTS = (".jpg", ".jpeg", ".png")


@dataclass
class ArtworkResult:
    fixes: list[tuple[Track, str, str, str]] = field(default_factory=list)   # (target, kind, donor, detail)
    no_donor: list[Track] = field(default_factory=list)
    unsupported: list[Track] = field(default_factory=list)
    ok: int = 0


def _needs(t: Track, cfg: Config) -> bool:
    a = cfg.artwork
    if not t.has_picture:
        return True
    return bool(a.replace_lower_res and a.min_resolution and t.pic_res and t.pic_res < a.min_resolution)


def _good_enough(donor: Track, target: Track, cfg: Config) -> bool:
    a = cfg.artwork
    if not donor.has_picture:
        return False
    if target.has_picture:               # replacing: donor must really be better
        return donor.pic_res > target.pic_res and donor.pic_res >= a.min_resolution
    return donor.pic_res >= a.min_resolution


def find_art_fixes(tracks: list[Track], cfg: Config, dupes: DedupeResult | None = None) -> ArtworkResult:
    a = cfg.artwork
    res = ArtworkResult()
    group_of: dict[str, list[Track]] = {}
    if "duplicate" in a.donor_order:
        dupes = dupes or find_duplicates(tracks, cfg)
        for g in dupes.groups:
            for m in g.members:
                group_of[m.path] = g.members
    albums: dict[tuple, list[Track]] = defaultdict(list)
    if "album_sibling" in a.donor_order:
        for t in tracks:
            if t.album:
                albums[(os.path.dirname(t.path), normalize_text(t.album))].append(t)
                albums[("*", normalize_text(t.album), normalize_text(t.albumartist or t.artist))].append(t)

    for t in tracks:
        if not _needs(t, cfg):
            res.ok += 1
            continue
        if t.fmt not in EMBED_EXTS:
            res.unsupported.append(t)
            continue
        found = None
        for src in a.donor_order:
            if src == "duplicate":
                pool = [m for m in group_of.get(t.path, []) if m is not t and _good_enough(m, t, cfg)]
                if pool:
                    best = max(pool, key=lambda m: m.pic_res)
                    found = ("duplicate", best.path, f"{best.pic_w}x{best.pic_h}")
            elif src == "album_sibling" and t.album:
                pool = albums.get((os.path.dirname(t.path), normalize_text(t.album)), []) + \
                       albums.get(("*", normalize_text(t.album), normalize_text(t.albumartist or t.artist)), [])
                pool = [m for m in pool if m is not t and _good_enough(m, t, cfg)]
                if pool:
                    best = max(pool, key=lambda m: m.pic_res)
                    found = ("album_sibling", best.path, f"{best.pic_w}x{best.pic_h}")
            elif src == "folder_image":
                img = _folder_image(os.path.dirname(t.path), a.folder_image_names)
                if img:
                    found = ("folder_image", img, os.path.basename(img))
            if found:
                break
        if found:
            res.fixes.append((t, *found))
        else:
            res.no_donor.append(t)
    return res


def _folder_image(folder: str, names: list[str]) -> str | None:
    try:
        files = {f.casefold(): f for f in os.listdir(folder)}
    except OSError:
        return None
    for n in names:
        for ext in IMG_EXTS:
            if (n + ext).casefold() in files:
                return os.path.join(folder, files[(n + ext).casefold()])
    return None


def build_plan(res: ArtworkResult, tracks: list[Track], cfg: Config) -> Plan:
    plan = Plan("artwork", meta={"fixed": len(res.fixes)})
    for t, kind, donor, detail in res.fixes:
        if kind == "folder_image":
            plan.actions.append(Action("embed_art", src="", dst=t.path, params={"image": donor}, note=f"from {detail}"))
        else:
            plan.actions.append(Action("embed_art", src=donor, dst=t.path, note=f"from {os.path.basename(donor)} ({kind}, {detail})"))
    if cfg.artwork.extract_to_folder:
        seen = set()
        for t in tracks:
            folder = os.path.dirname(t.path)
            if t.has_picture and folder not in seen:
                dst = os.path.join(folder, cfg.artwork.extract_name)
                if not os.path.exists(dst):
                    plan.actions.append(Action("extract_art", src=t.path, dst=dst, note="save cover as image"))
                seen.add(folder)
    return plan
