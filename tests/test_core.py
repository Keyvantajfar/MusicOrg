import os
from pathlib import Path

import pytest

from conftest import make_audio
from musicorg.core import artwork, convert, dedupe, journal, organize, reports, scanner, snapshot, tags
from musicorg.core.config import Config
from musicorg.core.executor import Executor
from musicorg.core.trash import RecycleBin

M = lambda title, artist, album, **kw: {"title": title, "artist": artist, "album": album, **kw}


def do_scan(cfg):
    return scanner.scan(cfg).tracks


# ------------------------------------------------------------------ config
def test_config_roundtrip(tmp_path):
    c = Config()
    c.scan.roots = ["/a b", "/ü"]
    c.convert.rules.append({"name": "x", "enabled": False, "min_size_mib": 1.5, "formats": "flac", "action": "skip"})
    p = c.save(tmp_path / "c.toml")
    assert Config.load(p).to_dict() == c.to_dict()


# -------------------------------------------------------------------- scan
def test_scan_reads_tags_and_uses_cache(cfg, lib):
    make_audio(lib / "a.flac", meta=M("Song", "Art", "Alb"), cover=300)
    make_audio(lib / "b.mp3", fmt="mp3", meta=M("Song2", "Art", "Alb"))
    make_audio(lib / "c.m4a", fmt="m4a", meta=M("Song3", "Art", "Alb"))
    r = scanner.scan(cfg)
    assert len(r.tracks) == 3 and r.read == 3
    a = next(t for t in r.tracks if t.fmt == "flac")
    assert (a.title, a.artist, a.album) == ("Song", "Art", "Alb")
    assert a.has_picture and a.pic_res == 300 and a.bit_depth == 16 and a.lossless
    assert not next(t for t in r.tracks if t.fmt == "mp3").lossless
    r2 = scanner.scan(cfg)
    assert r2.cached == 3 and r2.read == 0


def test_scan_exclude_and_formats(cfg, lib):
    make_audio(lib / "keep.flac", meta=M("A", "B", "C"))
    make_audio(lib / ".hidden" / "x.flac", meta=M("A", "B", "C"))
    assert [t.name for t in do_scan(cfg)] == ["keep.flac"]
    cfg.scan.formats = ["mp3"]
    assert do_scan(cfg) == []


# ------------------------------------------------------------------- dedupe
def test_dedupe_keeps_better_quality(cfg, lib):
    make_audio(lib / "lo.flac", bits=16, meta=M("Song", "Art", "Alb"))
    make_audio(lib / "hi.flac", bits=24, sr=48000, meta=M("SONG", "art", "alb"))
    make_audio(lib / "lossy.mp3", fmt="mp3", meta=M("Song", "Art", "Alb"))
    res = dedupe.find_duplicates(do_scan(cfg), cfg)
    assert len(res.groups) == 1
    g = res.groups[0]
    assert g.keeper.name == "hi.flac" and {t.name for t in g.losers} == {"lo.flac", "lossy.mp3"}


def test_dedupe_tie_prefers_picture(cfg, lib):
    make_audio(lib / "nopic.flac", meta=M("Song", "A", "B"))
    make_audio(lib / "pic.flac", meta=M("Song", "A", "B"), cover=200)
    g = dedupe.find_duplicates(do_scan(cfg), cfg).groups[0]
    assert g.keeper.name == "pic.flac"
    cfg.dedupe.criteria = ["size"]          # user reorders ranking: picture no longer matters
    g2 = dedupe.find_duplicates(do_scan(cfg), cfg).groups[0]
    assert g2.keeper.name == "nopic.flac" and "tie" in g2.reasons[-1]      # sizes within 5% = tie -> first path


def test_missing_tags_are_never_grouped(cfg, lib):
    make_audio(lib / "u1.flac", freq=300)                       # no tags at all
    make_audio(lib / "u2.flac", freq=500)
    make_audio(lib / "u3.flac", freq=700, meta=M("Unknown", "x", "y"))
    res = dedupe.find_duplicates(do_scan(cfg), cfg)
    assert res.groups == [] and len(res.review) == 3
    assert dedupe.build_plan(res, cfg).actions == []


def test_identical_bytes_dedupe_even_without_tags(cfg, lib):
    a = make_audio(lib / "u1.flac", freq=321)
    (lib / "u2.flac").write_bytes(a.read_bytes())
    res = dedupe.find_duplicates(do_scan(cfg), cfg)
    assert len(res.groups) == 1 and res.groups[0].exact and not res.review


def test_same_title_different_artist_or_duration_not_grouped(cfg, lib):
    make_audio(lib / "a.flac", meta=M("Intro", "Artist A", "X"))
    make_audio(lib / "b.flac", meta=M("Intro", "Artist B", "X"))
    make_audio(lib / "c.flac", dur=9.0, meta=M("Live", "Z", "Y"))
    make_audio(lib / "d.flac", dur=2.0, meta=M("Live", "Z", "Y"))
    res = dedupe.find_duplicates(do_scan(cfg), cfg)
    assert res.groups == []
    cfg.dedupe.duration_tolerance_s = 0
    assert len(dedupe.find_duplicates(do_scan(cfg), cfg).groups) == 1


def test_dedupe_apply_to_bin_and_undo(cfg, lib):
    make_audio(lib / "lo.flac", bits=16, meta=M("S", "A", "B"))
    make_audio(lib / "hi.flac", bits=24, meta=M("S", "A", "B"))
    res = dedupe.find_duplicates(do_scan(cfg), cfg)
    plan = dedupe.build_plan(res, cfg)
    assert [a.op for a in plan.actions] == ["trash"]
    r = Executor(cfg, dry_run=True).run(plan)
    assert (lib / "lo.flac").exists() and r.dry_run
    r = Executor(cfg).run(plan)
    assert not (lib / "lo.flac").exists() and len(RecycleBin(cfg).list()) == 1
    j = journal.Journal(cfg, "", jid=r.journal_id)
    journal.undo(cfg, j)
    assert (lib / "lo.flac").exists() and RecycleBin(cfg).list() == []


# ---------------------------------------------------------------------- bin
def test_bin_restore_purge_empty(cfg, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("hi")
    b = RecycleBin(cfg)
    i = b.send(f, "t")
    assert not f.exists() and b.total_size() == 2
    b.restore(i)
    assert f.read_text() == "hi"
    i = b.send(f)
    b.purge(i)
    assert b.list() == [] and not f.exists()
    b.send(tmp_path / "x.txt") if f.exists() else None


# ------------------------------------------------------------------ artwork
def test_artwork_from_duplicate_and_folder_image(cfg, lib):
    make_audio(lib / "keep.flac", bits=24, meta=M("S", "A", "B"))                 # best quality, no cover
    make_audio(lib / "copy.flac", bits=16, meta=M("S", "A", "B"), cover=500)       # loser with cover
    make_audio(lib / "alb" / "t1.flac", freq=200, meta=M("T1", "A", "Other"))
    (lib / "alb" / "cover.jpg").write_bytes(__import__("conftest")._jpeg(400))
    tracks = do_scan(cfg)
    res = artwork.find_art_fixes(tracks, cfg)
    kinds = {t.name: k for t, k, _, _ in res.fixes}
    assert kinds == {"keep.flac": "duplicate", "t1.flac": "folder_image"}
    plan = artwork.build_plan(res, tracks, cfg)
    r = Executor(cfg).run(plan)
    assert not r.failed
    assert all(t.has_picture for t in do_scan(cfg))


def test_artwork_min_resolution_replace(cfg, lib):
    make_audio(lib / "small.flac", meta=M("S", "A", "B"), cover=100)
    make_audio(lib / "big.flac", bits=24, meta=M("S", "A", "B"), cover=800)
    cfg.artwork.min_resolution, cfg.artwork.replace_lower_res = 500, True
    res = artwork.find_art_fixes(do_scan(cfg), cfg)
    assert [(t.name, k) for t, k, _, _ in res.fixes] == [("small.flac", "duplicate")]


# ----------------------------------------------------------------- organize
def names(plan):
    return sorted(os.path.relpath(a.dst, plan.meta["dest_root"]) for a in plan.actions if a.op != "cleanup")


def test_organize_basic_and_sanitize(cfg, lib):
    make_audio(lib / "1.flac", meta=M("T", "AC/DC", "Back: In Black?"))
    plan, rep = organize.build_plan(do_scan(cfg), cfg)
    assert names(plan) == ["AC_DC/Back_ In Black_/1.flac"]
    cfg.organize.replace_char = ""
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    assert names(plan) == ["ACDC/Back In Black/1.flac"]


def test_organize_greatest_hits_split_and_compilation(cfg, lib):
    for i in range(3):
        make_audio(lib / f"a{i}.flac", freq=200 + i, meta=M(f"A{i}", "Artist A", "Greatest Hits"))
        make_audio(lib / f"b{i}.flac", freq=300 + i, meta=M(f"B{i}", "Artist B", "greatest hits"))
    plan, rep = organize.build_plan(do_scan(cfg), cfg)
    assert {n.split("/")[0] for n in names(plan)} == {"Artist A", "Artist B"} and rep.split_albums
    cfg.organize.main_artist_rule = "most_frequent"             # old behaviour merges them
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    assert len({n.split("/")[0] for n in names(plan)}) == 1
    cfg.organize.main_artist_rule = "smart"
    for i in range(6):
        make_audio(lib / f"v{i}.flac", freq=400 + i, meta=M(f"V{i}", f"Various{i}", "Now 50"))
    plan, rep = organize.build_plan(do_scan(cfg), cfg)
    assert sum(n.startswith("Various Artists/Now 50/") for n in names(plan)) == 6 and rep.compilations == 1


def test_organize_albumartist_and_case_variants(cfg, lib):
    make_audio(lib / "1.flac", meta=M("T", "Main feat. X", "Album", albumartist="Main"))
    make_audio(lib / "2.flac", freq=500, meta=M("T2", "Main", "ALBUM", albumartist="Main"))
    make_audio(lib / "3.flac", freq=600, meta=M("T3", "Main", "album", albumartist="Main"))
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    assert len({os.path.dirname(n) for n in names(plan)}) == 1


def test_organize_unknown_to_unsorted_and_template(cfg, lib):
    make_audio(lib / "mystery.flac")
    make_audio(lib / "ok.flac", freq=500, meta=M("Title", "Art", "Alb", tracknumber="x") if False else M("Title", "Art", "Alb"))
    plan, rep = organize.build_plan(do_scan(cfg), cfg)
    assert "_Unsorted/mystery.flac" in names(plan) and rep.unsorted == 1
    cfg.organize.unknown_policy = "defaults"
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    assert "Unknown Artist/Unknown Album/mystery.flac" in names(plan)
    cfg.organize.unknown_policy = "skip"
    plan, rep = organize.build_plan(do_scan(cfg), cfg)
    assert rep.skipped == 1
    cfg.organize.template = "{artist} - {title}"
    cfg.organize.unknown_policy = "defaults"
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    assert "Art - Title.flac" in names(plan)


def test_organize_collision_policies(cfg, lib):
    make_audio(lib / "x" / "s.flac", meta=M("T", "A", "B"))
    make_audio(lib / "y" / "s.flac", freq=500, meta=M("T2", "A", "B"))
    plan, rep = organize.build_plan(do_scan(cfg), cfg)
    assert names(plan) == ["A/B/s (2).flac", "A/B/s.flac"] and rep.collisions == 1
    cfg.organize.collision = "skip"
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    assert names(plan) == ["A/B/s.flac"]


def test_organize_move_undo_and_empty_dirs(cfg, lib):
    make_audio(lib / "deep" / "dir" / "s.flac", meta=M("T", "A", "B"))
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    r = Executor(cfg).run(plan, "move")
    out = Path(cfg.organize.dest_root) / "A" / "B" / "s.flac"
    assert out.exists() and not (lib / "deep").exists()          # empty dirs cleaned
    journal.undo(cfg, journal.Journal(cfg, "", jid=r.journal_id))
    assert (lib / "deep" / "dir" / "s.flac").exists() and not out.exists()
    assert not Path(cfg.organize.dest_root, "A").exists()


def test_hardlink_preview_finalize_and_discard(cfg, lib):
    make_audio(lib / "s.flac", meta=M("T", "A", "B"))
    cfg.organize.mode = "hardlink"
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    r = Executor(cfg).run(plan, "hardlink")
    out = Path(cfg.organize.dest_root) / "A" / "B" / "s.flac"
    assert out.exists() and (lib / "s.flac").exists() and os.path.samefile(out, lib / "s.flac")
    j = journal.Journal(cfg, "", jid=r.journal_id)
    assert j in [x for x in journal.pending_previews(cfg)] or [x.id for x in journal.pending_previews(cfg)] == [j.id]
    journal.discard(cfg, j)
    assert not out.exists() and (lib / "s.flac").exists()          # originals untouched
    r = Executor(cfg).run(plan, "hardlink")
    journal.finalize(cfg, journal.Journal(cfg, "", jid=r.journal_id))
    assert out.exists() and not (lib / "s.flac").exists()          # old name gone, data kept


def test_symlink_preview_finalize(cfg, lib):
    make_audio(lib / "s.flac", meta=M("T", "A", "B"))
    cfg.organize.mode = "symlink"
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    r = Executor(cfg).run(plan, "symlink")
    out = Path(cfg.organize.dest_root) / "A" / "B" / "s.flac"
    assert out.is_symlink()
    journal.finalize(cfg, journal.Journal(cfg, "", jid=r.journal_id))
    assert out.exists() and not out.is_symlink() and not (lib / "s.flac").exists()


def test_hardlink_fallback_to_copy(cfg, lib, monkeypatch):
    make_audio(lib / "s.flac", meta=M("T", "A", "B"))
    cfg.organize.mode, cfg.organize.link_fallback = "hardlink", "copy"
    monkeypatch.setattr(os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("cross-device")))
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    r = Executor(cfg).run(plan)
    out = Path(cfg.organize.dest_root) / "A" / "B" / "s.flac"
    assert out.exists() and not out.is_symlink() and not r.failed
    assert journal.Journal(cfg, "", jid=r.journal_id).entries()[0]["op"] == "copy"


# ------------------------------------------------------------------ convert
def test_convert_rules_incremental_and_no_upsample(cfg, lib, tmp_path):
    make_audio(lib / "hires.flac", bits=24, sr=96000, meta=M("H", "A", "B"))
    make_audio(lib / "cd.flac", bits=16, sr=44100, meta=M("C", "A", "B"), freq=500)
    make_audio(lib / "lossy.mp3", fmt="mp3", meta=M("L", "A", "B"), freq=600)
    cfg.convert.target_root = str(tmp_path / "ipod")
    cfg.convert.rules = [dict(convert.NEW_RULE, name="hires", min_sample_rate=48000, sample_rate=44100, bit_depth=16, channels=2)]
    plan, rep = convert.build_plan(do_scan(cfg), cfg)
    assert (rep.convert, rep.copy) == (1, 2)
    r = Executor(cfg, workers=2).run(plan)
    assert not r.failed, r.failed
    out = scanner.tags.read_track(tmp_path / "ipod" / "hires.flac")
    assert (out.sample_rate, out.bit_depth) == (44100, 16) and out.title == "H"
    cd = scanner.tags.read_track(tmp_path / "ipod" / "cd.flac")
    assert cd.sample_rate == 44100
    plan2, rep2 = convert.build_plan(do_scan(cfg), cfg)
    assert rep2.up_to_date == 3 and not plan2.actions                  # incremental


def test_convert_to_mp3_flat_and_orphans(cfg, lib, tmp_path):
    make_audio(lib / "a" / "x.flac", meta=M("X", "A", "B"), cover=200)
    make_audio(lib / "b" / "x.flac", freq=700, meta=M("X2", "A", "B"))
    tgt = tmp_path / "dev"
    cfg.convert.target_root, cfg.convert.layout, cfg.convert.delete_orphans = str(tgt), "flat", True
    cfg.convert.rules = [dict(convert.NEW_RULE, name="all", codec="mp3", bitrate_kbps=128, sample_rate=0, bit_depth=0, channels=0)]
    (tgt).mkdir()
    (tgt / "old.mp3").write_bytes(b"x" * 10)
    plan, rep = convert.build_plan(do_scan(cfg), cfg)
    assert rep.orphans == 1 and rep.convert == 2
    r = Executor(cfg, workers=2).run(plan)
    assert not r.failed, r.failed
    assert sorted(p.name for p in tgt.iterdir()) == ["x (2).mp3", "x.mp3"] and len(RecycleBin(cfg).list()) == 1
    assert scanner.tags.read_track(tgt / "x.mp3").has_picture or scanner.tags.read_track(tgt / "x (2).mp3").has_picture


def test_convert_failure_leaves_no_partial(cfg, lib, tmp_path):
    lib.mkdir(parents=True)
    (lib / "bad.flac").write_bytes(b"not audio")
    from musicorg.core.models import Track
    t = Track(str(lib / "bad.flac"), size=9, fmt="flac")
    cfg.convert.target_root = str(tmp_path / "d")
    cfg.convert.rules = [dict(convert.NEW_RULE)]
    plan, _ = convert.build_plan([t], cfg)
    r = Executor(cfg).run(plan)
    assert r.failed and not list((tmp_path / "d").glob("*"))


# ----------------------------------------------------------------- snapshot
def test_snapshot_export_and_restore(cfg, lib, tmp_path):
    for i, (ar, al) in enumerate([("A", "X"), ("B", "Y/Z")]):
        make_audio(lib / "flat" / f"{i}.flac", freq=200 + i * 50, meta=M(f"T{i}", ar, al))
    plan, _ = organize.build_plan(do_scan(cfg), cfg)
    Executor(cfg).run(plan)
    out = Path(cfg.organize.dest_root)
    snap_path = tmp_path / "snap.json"
    snap = snapshot.export_structure(str(out), str(snap_path), ["flac"])
    assert set(snap["structure"]) == {"A", "B"}
    # scramble: dump everything in one folder, then restore from the snapshot
    flat = tmp_path / "flat"
    for f in out.rglob("*.flac"):
        flat.mkdir(exist_ok=True)
        f.rename(flat / f.name)
    rplan, rep = snapshot.restore_plan(snapshot.load_snapshot(str(snap_path)), str(flat), str(tmp_path / "ipod"), cfg)
    assert rep.found == 2 and not rep.missing
    Executor(cfg).run(rplan)
    assert len(list((tmp_path / "ipod").rglob("*.flac"))) == 2


def test_snapshot_loads_old_format(tmp_path):
    p = tmp_path / "old.json"
    p.write_text('{"A": {"B": ["x.flac"]}}')
    assert snapshot.load_snapshot(str(p))["structure"]["A"]["B"] == ["x.flac"]


# ------------------------------------------------------------ tags / reports
def test_edit_tags_via_plan_and_undo(cfg, lib):
    from musicorg.core.plan import Action, Plan
    f = make_audio(lib / "u.flac")
    r = Executor(cfg).run(Plan("tag", [Action("tag", src=str(f), params={"values": {"title": "New", "artist": "Me"}})]))
    t = tags.read_track(f)
    assert (t.title, t.artist) == ("New", "Me")
    journal.undo(cfg, journal.Journal(cfg, "", jid=r.journal_id))
    assert tags.read_track(f).title == ""


def test_reports(cfg, lib):
    make_audio(lib / "a.flac", meta=M("T", "A", "B"))
    make_audio(lib / "b.mp3", fmt="mp3")
    s = reports.library_stats(do_scan(cfg), cfg)
    assert s["tracks"] == 2 and s["lossless"] == 1 and s["missing_tags"]["title"] == 1
    p = reports.export_tracks(do_scan(cfg), str(Path(cfg.data_dir()) / "t.csv"))
    assert p.read_text().count("\n") == 3
