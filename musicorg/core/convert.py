"""Device sync: rules decide per file whether to copy, link, convert or skip.

Rules are fully user-defined (Convert tab). Each rule has optional conditions
(0 / empty = ignored) and an action. First enabled matching rule wins.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

from .config import AUDIO_EXTS, Config
from .models import Track
from .plan import Action, Plan
from .util import sanitize_component

CODECS = {          # codec -> (extension, ffmpeg encoder, lossy?)
    "flac": ("flac", "flac", False),
    "alac": ("m4a", "alac", False),
    "wav": ("wav", "pcm_s16le", False),
    "mp3": ("mp3", "libmp3lame", True),
    "aac": ("m4a", "aac", True),
    "opus": ("opus", "libopus", True),
}
COVER_OK = {"flac", "mp3", "alac", "aac"}

NEW_RULE = {"name": "New rule", "enabled": True, "min_size_mib": 0.0, "max_size_mib": 0.0, "min_sample_rate": 0,
            "min_bit_depth": 0, "formats": "", "action": "convert", "codec": "flac", "sample_rate": 44100,
            "bit_depth": 16, "channels": 2, "bitrate_kbps": 0}


def rule_matches(rule: dict, t: Track) -> bool:
    if not rule.get("enabled", True):
        return False
    if rule.get("min_size_mib") and t.size_mib < float(rule["min_size_mib"]):
        return False
    if rule.get("max_size_mib") and t.size_mib > float(rule["max_size_mib"]):
        return False
    if rule.get("min_sample_rate") and t.sample_rate < int(rule["min_sample_rate"]):
        return False
    if rule.get("min_bit_depth") and t.bit_depth < int(rule["min_bit_depth"]):
        return False
    fm = [f.strip().lower().lstrip(".") for f in str(rule.get("formats", "")).split(",") if f.strip()]
    if fm and t.fmt not in fm:
        return False
    return True


def decide(t: Track, cfg: Config) -> tuple[str, dict, str]:
    """-> (action, params, rule name)"""
    for r in cfg.convert.rules:
        if rule_matches(r, t):
            return r.get("action", "copy"), r, r.get("name", "rule")
    return cfg.convert.default_action, {}, "default"


@dataclass
class SyncReport:
    copy: int = 0
    link: int = 0
    convert: int = 0
    skip: int = 0
    up_to_date: int = 0
    orphans: int = 0
    est_src_bytes: int = 0       # bytes of files that will be converted
    by_rule: dict = field(default_factory=dict)


def _eff_params(t: Track, r: dict, cfg: Config) -> dict:
    codec = r.get("codec", "flac")
    sr, bd, ch = int(r.get("sample_rate", 0) or 0), int(r.get("bit_depth", 0) or 0), int(r.get("channels", 0) or 0)
    if cfg.convert.no_upsample:
        if sr and t.sample_rate and sr > t.sample_rate:
            sr = 0
        if bd and t.bit_depth and bd > t.bit_depth:
            bd = 0
        if ch and t.channels and ch > t.channels:
            ch = 0
    return {"codec": codec, "sample_rate": sr, "bit_depth": bd, "channels": ch,
            "bitrate_kbps": int(r.get("bitrate_kbps", 0) or 0), "src_bit_depth": t.bit_depth}


def _target_rel(t: Track, src_root: str, cfg: Config, ext: str, tracks_owner=None) -> str:
    c = cfg.convert
    if c.layout == "flat":
        rel = os.path.basename(t.path)
    else:
        rel = os.path.relpath(t.path, src_root) if src_root else os.path.basename(t.path)
    base, _ = os.path.splitext(rel)
    parts = (base + "." + ext).replace("\\", "/").split("/")
    return "/".join(sanitize_component(p, profile=c.sanitize_profile, repl="_", norm="NFC", keep_ext=(i == len(parts) - 1),
                                       max_len=150) for i, p in enumerate(parts))


def build_plan(tracks: list[Track], cfg: Config, target_root: str | None = None) -> tuple[Plan, SyncReport]:
    c = cfg.convert
    root = os.path.expanduser(target_root or c.target_root)
    if not root:
        raise ValueError("Set convert.target_root first")
    roots = [os.path.expanduser(r) for r in cfg.scan.roots]
    rep = SyncReport()
    plan = Plan("sync", meta={"target_root": root})
    used: set[str] = set()
    expected: set[str] = set()

    def src_root_of(t):
        for r in roots:
            if t.path.startswith(r.rstrip("/") + "/"):
                return r
        return os.path.dirname(t.path)

    for t in tracks:
        action, rule, rname = decide(t, cfg)
        rep.by_rule[rname] = rep.by_rule.get(rname, 0) + 1
        if action == "skip":
            rep.skip += 1
            continue
        params = {}
        if action == "convert":
            params = _eff_params(t, rule, cfg)
            ext = CODECS[params["codec"]][0]
        else:
            ext = t.fmt
        rel = _target_rel(t, src_root_of(t), cfg, ext)
        dst = os.path.join(root, *rel.split("/"))
        if dst.casefold() in used:                              # flat layout collisions
            stem, e = os.path.splitext(dst)
            i = 2
            while f"{stem} ({i}){e}".casefold() in used:
                i += 1
            dst = f"{stem} ({i}){e}"
        used.add(dst.casefold())
        expected.add(dst)
        if c.incremental and _up_to_date(dst, t):
            rep.up_to_date += 1
            continue
        if action == "convert":
            rep.convert += 1
            rep.est_src_bytes += t.size
            plan.actions.append(Action("convert", src=t.path, dst=dst, params=params, note=f"rule: {rname}"))
        elif action == "link":
            rep.link += 1
            plan.actions.append(Action(c.link_mode, src=t.path, dst=dst, params={"overwrite": True}, note=f"rule: {rname}"))
        else:
            rep.copy += 1
            plan.actions.append(Action("copy", src=t.path, dst=dst, params={"overwrite": True}, note=f"rule: {rname}"))

    if c.delete_orphans and os.path.isdir(root):
        exts = {"." + e for e in AUDIO_EXTS}
        for dp, _dn, fns in os.walk(root):
            for fn in fns:
                full = os.path.join(dp, fn)
                if os.path.splitext(fn)[1].lower() in exts and full not in expected:
                    rep.orphans += 1
                    plan.actions.append(Action("trash" if c.orphan_action == "bin" else "delete", src=full, note="orphan on device"))
    return plan, rep


def _up_to_date(dst: str, t: Track) -> bool:
    """Target exists, is non-empty and not older than the source (2 s slack: FAT32 stores 2 s resolution)."""
    try:
        st = os.stat(dst)
    except OSError:
        return False
    return st.st_size > 0 and st.st_mtime_ns >= t.mtime_ns - 2_000_000_000


# ------------------------------------------------------------------ ffmpeg
def ffmpeg_cmd(src: str, dst: str, p: dict, cfg: Config) -> list[str]:
    codec = p.get("codec", "flac")
    _ext, enc, lossy = CODECS[codec]
    cmd = [cfg.convert.ffmpeg_path, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src, "-map", "0:a:0"]
    if codec in COVER_OK:
        cmd += ["-map", "0:v:0?", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    cmd += ["-map_metadata", "0", "-c:a", enc]
    bd, sr, ch = p.get("bit_depth", 0), p.get("sample_rate", 0), p.get("channels", 0)
    if not lossy and codec in ("flac", "alac", "wav"):
        eff_bd = bd or p.get("src_bit_depth", 0)
        if bd:
            if codec == "wav":
                cmd[cmd.index(enc)] = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}.get(bd, "pcm_s16le")
            elif codec == "alac":
                cmd += ["-sample_fmt", "s16p" if bd <= 16 else "s32p"]
            else:
                cmd += ["-sample_fmt", "s16" if bd <= 16 else "s32"]
                if bd == 24:
                    cmd += ["-bits_per_raw_sample", "24"]
        if bd and bd <= 16 and (p.get("src_bit_depth", 0) or 24) > 16 and cfg.convert.dither:
            cmd += ["-af", "aresample=dither_method=triangular"]
    if lossy and p.get("bitrate_kbps"):
        cmd += ["-b:a", f"{int(p['bitrate_kbps'])}k"]
    elif lossy and codec == "mp3":
        cmd += ["-q:a", "0"]
    if sr:
        cmd += ["-ar", str(sr)]
    if ch:
        cmd += ["-ac", str(ch)]
    cmd.append(dst)
    return cmd


def convert_file(src: str, dst: str, params: dict, cfg: Config) -> None:
    """Convert atomically: write a .partial file, then rename."""
    if shutil.which(cfg.convert.ffmpeg_path) is None and not os.path.exists(cfg.convert.ffmpeg_path):
        raise FileNotFoundError(f"ffmpeg not found ({cfg.convert.ffmpeg_path})")
    stem, ext = os.path.splitext(dst)
    tmp = f"{stem}.partial{ext}"
    cmd = ffmpeg_cmd(src, tmp, params, cfg)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError("ffmpeg failed: " + (proc.stderr.strip().splitlines() or ["unknown error"])[-1])
    os.replace(tmp, dst)
