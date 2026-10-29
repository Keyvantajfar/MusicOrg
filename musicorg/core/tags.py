"""Reading and writing audio tags / cover art for FLAC, MP3, M4A, OGG/Opus, WAV, AIFF."""
from __future__ import annotations

import base64
import io
import re
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TPE2
from mutagen.mp4 import MP4, MP4Cover

from .models import Track

try:
    from PIL import Image
except Exception:  # Pillow is optional
    Image = None

LOSSLESS_EXT = {"flac", "wav", "aiff", "aif", "wv", "ape"}
_MP4 = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "albumartist": "aART",
        "genre": "\xa9gen", "year": "\xa9day"}
_ID3 = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
        "genre": "TCON", "year": "TDRC"}
_VORBIS = {"title": ("title",), "artist": ("artist",), "album": ("album",),
           "albumartist": ("albumartist", "album artist"), "genre": ("genre",),
           "year": ("date", "year")}


class Unsupported(Exception):
    pass


def _family(audio) -> str:
    if isinstance(audio, MP4):
        return "mp4"
    tags = audio.tags
    if isinstance(tags, ID3) or (tags is None and type(audio).__name__ in ("MP3", "WAVE", "AIFF", "TrueAudio")):
        return "id3"
    return "vorbis"


def _first(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return str(v).strip()


def _num(s: str) -> int:
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else 0


def _text(audio, fam: str, key: str) -> str:
    tags = audio.tags
    if tags is None:
        return ""
    try:
        if fam == "mp4":
            return _first(tags.get(_MP4[key]))
        if fam == "id3":
            fr = tags.get(_ID3[key])
            return _first(fr.text) if fr is not None else ""
        for k in _VORBIS[key]:
            v = tags.get(k)
            if v:
                return _first(v)
    except Exception:
        pass
    return ""


def _track_disc(audio, fam: str) -> tuple[int, int]:
    tags = audio.tags
    if tags is None:
        return 0, 0
    try:
        if fam == "mp4":
            trkn, disk = tags.get("trkn"), tags.get("disk")
            return (trkn[0][0] if trkn else 0), (disk[0][0] if disk else 0)
        if fam == "id3":
            t, d = tags.get("TRCK"), tags.get("TPOS")
            return (_num(_first(t.text)) if t else 0), (_num(_first(d.text)) if d else 0)
        return (_num(_first(tags.get("tracknumber"))), _num(_first(tags.get("discnumber"))))
    except Exception:
        return 0, 0


def _pictures(audio, fam: str) -> list[tuple[bytes, str, int]]:
    """Return [(data, mime, picture_type)]"""
    out: list[tuple[bytes, str, int]] = []
    tags = audio.tags
    try:
        if isinstance(audio, FLAC):
            out = [(p.data, p.mime, p.type) for p in audio.pictures]
        elif fam == "id3" and tags is not None:
            out = [(f.data, f.mime, f.type) for f in tags.getall("APIC")]
        elif fam == "mp4" and tags is not None:
            for c in tags.get("covr", []):
                out.append((bytes(c), "image/png" if c.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg", 3))
        elif tags is not None and "metadata_block_picture" in tags:
            for b64 in tags["metadata_block_picture"]:
                p = Picture(base64.b64decode(b64))
                out.append((p.data, p.mime, p.type))
    except Exception:
        pass
    return out


def _best(pics):
    if not pics:
        return None
    front = [p for p in pics if p[2] == 3]
    return (front or pics)[0]


def _img_size(data: bytes) -> tuple[int, int]:
    if Image is None:
        return 0, 0
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return 0, 0


def read_track(path: str | Path, read_pic_size: bool = True) -> Track:
    p = Path(path)
    st = p.stat()
    ext = p.suffix.lower().lstrip(".")
    t = Track(path=str(p), size=st.st_size, mtime_ns=st.st_mtime_ns, fmt=ext)
    try:
        audio = mutagen.File(p)
        if audio is None:
            t.error = "unreadable / unsupported"
            return t
        fam = _family(audio)
        t.title, t.artist = _text(audio, fam, "title"), _text(audio, fam, "artist")
        t.album, t.albumartist = _text(audio, fam, "album"), _text(audio, fam, "albumartist")
        t.genre, t.year = _text(audio, fam, "genre"), _text(audio, fam, "year")[:4]
        t.track, t.disc = _track_disc(audio, fam)
        info = audio.info
        t.duration = float(getattr(info, "length", 0) or 0)
        t.bitrate = int((getattr(info, "bitrate", 0) or 0) // 1000)
        t.sample_rate = int(getattr(info, "sample_rate", 0) or 0)
        t.channels = int(getattr(info, "channels", 0) or 0)
        t.bit_depth = int(getattr(info, "bits_per_sample", 0) or 0)
        codec = str(getattr(info, "codec", "") or "").lower()
        t.lossless = ext in LOSSLESS_EXT or codec == "alac"
        pics = _pictures(audio, fam)
        t.has_picture = bool(pics)
        if pics and read_pic_size:
            t.pic_w, t.pic_h = _img_size(_best(pics)[0])
    except Exception as e:  # corrupt file etc: keep the row, flag it
        t.error = f"{type(e).__name__}: {e}"
    return t


def read_cover(path: str | Path) -> tuple[bytes, str] | None:
    """Best embedded cover of a file as (bytes, mime)."""
    try:
        audio = mutagen.File(path)
        if audio is None:
            return None
        b = _best(_pictures(audio, _family(audio)))
        return (b[0], b[1]) if b else None
    except Exception:
        return None


def raw_tags(path: str | Path) -> dict:
    """Every tag as plain strings (for the preview panel)."""
    out: dict = {}
    try:
        audio = mutagen.File(path)
        if audio is None or audio.tags is None:
            return out
        for k, v in audio.tags.items():
            if k.startswith("APIC") or k in ("covr", "metadata_block_picture"):
                continue
            s = _first(getattr(v, "text", v))
            out[str(k)] = s[:200]
    except Exception:
        pass
    return out


def _mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def embed_cover(path: str | Path, data: bytes, mime: str | None = None) -> None:
    mime = mime or _mime(data)
    audio = mutagen.File(path)
    if audio is None:
        raise Unsupported("unreadable file")
    fam = _family(audio)
    if isinstance(audio, FLAC):
        pic = Picture()
        pic.type, pic.mime, pic.data, pic.desc = 3, mime, data, "Cover"
        pic.width, pic.height = _img_size(data)
        pic.depth = 24
        audio.clear_pictures()
        audio.add_picture(pic)
    elif fam == "id3":
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    elif fam == "mp4":
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        if audio.tags is None:
            audio.add_tags()
        audio.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
    else:
        raise Unsupported(f"embedding covers into .{Path(path).suffix.lstrip('.')} is not supported")
    audio.save()


def write_tags(path: str | Path, values: dict) -> dict:
    """Write title/artist/album/albumartist. Returns the old values (for undo)."""
    audio = mutagen.File(path)
    if audio is None:
        raise Unsupported("unreadable file")
    fam = _family(audio)
    old = {k: _text(audio, fam, k) for k in values}
    if fam == "id3":
        if audio.tags is None:
            audio.add_tags()
        frames = {"title": TIT2, "artist": TPE1, "album": TALB, "albumartist": TPE2}
        for k, v in values.items():
            audio.tags.delall(_ID3[k])
            if v:
                audio.tags.add(frames[k](encoding=3, text=[v]))
    elif fam == "mp4":
        if audio.tags is None:
            audio.add_tags()
        for k, v in values.items():
            if v:
                audio.tags[_MP4[k]] = [v]
            else:
                audio.tags.pop(_MP4[k], None)
    else:
        if audio.tags is None:
            audio.add_tags()
        for k, v in values.items():
            key = _VORBIS[k][0]
            if v:
                audio.tags[key] = [v]
            elif key in audio.tags:
                del audio.tags[key]
    audio.save()
    return old
