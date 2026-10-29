import io
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from musicorg.core import tags  # noqa: E402
from musicorg.core.config import Config  # noqa: E402


def _jpeg(size=600, color=(200, 30, 30)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG")
    return buf.getvalue()


def make_audio(path: Path, *, fmt="flac", sr=44100, bits=16, dur=2.0, freq=440, meta=None, cover=0, cover_color=(200, 30, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}", "-ar", str(sr)]
    if fmt == "flac":
        cmd += ["-c:a", "flac", "-sample_fmt", "s16" if bits == 16 else "s32"] + (["-bits_per_raw_sample", "24"] if bits == 24 else [])
    elif fmt == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", "192k"]
    elif fmt == "m4a":
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    if meta:
        tags.write_tags(path, meta)
    if cover:
        tags.embed_cover(path, _jpeg(cover, cover_color))
    return path


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSICORG_HOME", str(tmp_path / "home"))
    c = Config()
    c.scan.roots = [str(tmp_path / "lib")]
    c.organize.dest_root = str(tmp_path / "out")
    c.general.threads = 2
    return c


@pytest.fixture
def lib(tmp_path):
    return tmp_path / "lib"
