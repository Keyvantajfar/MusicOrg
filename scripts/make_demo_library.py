"""Create a small fake music library (real FLAC/MP3 files with tags, covers, duplicates, unknown files)
so you can try MusicOrg without risking your real collection.   usage: python scripts/make_demo_library.py /tmp/demo-music
"""
import io
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from musicorg.core import tags  # noqa: E402


def jpeg(size, color):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (size, size), color)
    d = ImageDraw.Draw(im)
    for i in range(0, size // 2, size // 10):
        d.rectangle([i, i, size - i, size - i], outline=tuple(255 - c for c in color), width=max(2, size // 40))
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


def make(path, fmt="flac", sr=44100, bits=16, dur=3, freq=440, meta=None, cover=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}", "-ar", str(sr)]
    cmd += (["-c:a", "flac", "-sample_fmt", "s16" if bits == 16 else "s32"] + (["-bits_per_raw_sample", "24"] if bits == 24 else [])
            if fmt == "flac" else ["-c:a", "libmp3lame", "-b:a", "192k"])
    subprocess.run(cmd + [str(path)], check=True)
    if meta:
        tags.write_tags(path, meta)
    if cover:
        tags.embed_cover(path, jpeg(*cover))


def main(root: Path):
    flat = root / "all-in-one-folder"          # like the original problem: everything in one dir
    n = 0

    def track(title, artist, album, **kw):
        nonlocal n
        n += 1
        fmt = kw.pop("fmt", "flac")
        cover = kw.pop("cover", None)
        audio = {k: kw.pop(k) for k in ("sr", "bits", "dur") if k in kw}
        make(flat / f"{n:02d} {title}.{fmt}".replace("/", "-").replace("?", ""), fmt=fmt, freq=200 + n * 37,
             meta=dict(title=title, artist=artist, album=album, **kw), cover=cover, **audio)

    red, blue, green = (200, 50, 60), (50, 90, 200), (50, 160, 90)
    for i, t in enumerate(["Neon Rain", "Glass Hours", "Paper Moon"], 1):
        track(t, "The Static Bloom", "Night Drive", cover=(500, red))
    track("Neon Rain", "The Static Bloom", "Night Drive", bits=24, sr=96000, cover=(900, red))      # hi-res duplicate
    track("Glass Hours", "The Static Bloom", "night drive", fmt="mp3")                              # lossy dupe, case variant
    for t in ["Opening", "Harbor Lights"]:
        track(t, "Mara Lind", "Greatest Hits", cover=(400, blue))
    for t in ["Opening Act", "Last Train"]:
        track(t, "Orchard Kings", "Greatest Hits", cover=(400, green))                              # same album name, other artist
    track("Paper Moon", "The Static Bloom", "Night Drive")                                          # no cover, duplicate of earlier
    for i in range(6):
        track(f"Hit {i}", f"Various Band {i}", "Now That's Music", cover=(300, (120, 80 + i * 20, 200)))
    track("AC/DC Tribute?", "Tribute: Band", "Back/Front: Live?")                                   # illegal characters
    make(flat / "99 mystery.flac", freq=777)                                                       # no tags at all
    make(flat / "98 untitled.flac", freq=555, meta={"title": "Unknown"})
    print(f"demo library with {n + 2} files in {flat}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "demo-music"))
