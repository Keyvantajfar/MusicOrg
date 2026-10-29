import asyncio
import os
import sys
from pathlib import Path

import pytest

from conftest import make_audio
from musicorg.core.config import Config
from musicorg.tui.app import MusicOrgApp

M = lambda title, artist, album, **kw: {"title": title, "artist": artist, "album": album, **kw}
pytestmark = pytest.mark.asyncio


async def settle(pilot, n=30):
    for _ in range(n):
        await pilot.pause(0.05)
        if not pilot.app.busy:
            break
    await pilot.pause(0.1)


@pytest.fixture
def app(cfg, lib):
    make_audio(lib / "a.flac", bits=24, meta=M("Song", "Art", "Alb"))
    make_audio(lib / "b.flac", bits=16, meta=M("Song", "Art", "Alb"), cover=300)
    make_audio(lib / "c.flac", freq=500, meta=M("Other", "Art", "Alb"), cover=300)
    make_audio(lib / "mystery.flac", freq=900)
    cfg.path = Path(cfg.data_dir()) / "config.toml"
    return MusicOrgApp(cfg)


async def test_home_and_workspace(app):
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()
        from musicorg.tui.home import HomeScreen
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("2")
        await pilot.pause()
        from musicorg.tui.workspace import WorkspaceScreen
        assert isinstance(app.screen, WorkspaceScreen)
        tabs = app.screen.query_one("#tabs")
        # locked until scan
        assert tabs.get_tab("duplicates").disabled and tabs.get_tab("library").disabled is False
        app.scan_library(True)
        await settle(pilot)
        assert len(app.state.tracks) == 4
        assert not tabs.get_tab("duplicates").disabled


async def test_cleanup_flow_with_bin(app, lib):
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.press("2")
        await pilot.pause()
        app.scan_library(True)
        await settle(pilot)
        app.screen.query_one("#tabs").active = "duplicates"
        await pilot.pause()
        await pilot.click("#find")
        await settle(pilot)
        d = app.state.dupes
        assert d and len(d.groups) == 1 and len(d.review) == 1
        assert d.groups[0].keeper.name == "a.flac"
        await pilot.click("#apply")
        await pilot.pause(0.3)
        from musicorg.tui.modals import PlanReviewScreen
        assert isinstance(app.screen, PlanReviewScreen)
        await pilot.click("#apply")
        await settle(pilot, 60)
        assert not (lib / "b.flac").exists() and (lib / "a.flac").exists()
        assert len(app.bin().list()) == 1
        await pilot.click("#close")
        for _ in range(100):           # auto rescan + re-find runs asynchronously after the dialog closes
            await pilot.pause(0.05)
            if len(app.state.tracks) == 3 and not app.busy:
                break
        assert len(app.state.tracks) == 3


async def test_settings_roundtrip(app):
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()
        from musicorg.tui.settings import SettingsScreen
        assert isinstance(app.screen, SettingsScreen)
        inp = app.screen.query_one("#set-scan__min_size_kib")
        inp.value = "64"
        sw = app.screen.query_one("#set-dedupe__exact_hash")
        sw.value = False
        app.screen.action_save()
        assert app.cfg.scan.min_size_kib == 64 and app.cfg.dedupe.exact_hash is False
        assert Config.load(app.cfg.path).scan.min_size_kib == 64


async def test_organize_preview_in_tui(app, lib, cfg):
    cfg.organize.mode = "hardlink"
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.press("3")
        await pilot.pause()
        app.scan_library(True)
        await settle(pilot)
        app.screen.query_one("#tabs").active = "organize"
        await pilot.pause()
        await pilot.click("#preview")
        await settle(pilot)
        assert app.state.org_plan and len(app.state.org_plan.actions) >= 4


async def test_sync_preview_and_rule_in_tui(app, cfg, tmp_path):
    cfg.convert.target_root = str(tmp_path / "ipod")
    cfg.convert.rules[0]["min_size_mib"] = 0.0005          # ~0.5 KiB -> every file matches
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.press("4")
        await pilot.pause()
        app.scan_library(True)
        await settle(pilot)
        app.screen.goto("sync")
        await pilot.pause()
        pane = app.screen.query_one("#pane-sync")
        await pilot.click(pane.query_one("#preview").__class__ and "#pane-sync #preview")
        await settle(pilot)
        plan = app.state.sync_plan
        assert plan and all(a.op == "convert" for a in plan.actions) and len(plan.actions) == 4
        assert not pane.query_one("#apply").disabled
