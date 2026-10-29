"""The pipeline tabs. Every option control is bound to the config via its id: cfg-<section>__<field>."""
from __future__ import annotations

import os
import time
from dataclasses import fields
from pathlib import Path

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, VerticalScroll
from textual.widgets import Button, Collapsible, DataTable, Input, Label, Select, Static, Switch

from ..core import artwork as art
from ..core import convert as cv
from ..core import dedupe as dd
from ..core import journal as jr
from ..core import organize as org
from ..core import reports, snapshot
from ..core.config import SECTIONS
from ..core.executor import Executor
from ..core.plan import Action, Plan
from ..core.util import short_size
from .modals import ConfirmModal, PlanReviewScreen, RuleEditModal, TagEditModal, short

ROW_LIMIT = 1500


# ------------------------------------------------------------ small helpers
def _fmt(v) -> str:
    return ", ".join(map(str, v)) if isinstance(v, list) else str(v)


def _meta(sec: str, name: str):
    return next(f.metadata for f in fields(SECTIONS[sec]) if f.name == name)


def cinput(cfg, sec, name, label, placeholder="", wide=False) -> Horizontal:
    i = Input(value=_fmt(getattr(getattr(cfg, sec), name)), id=f"cfg-{sec}__{name}", placeholder=placeholder, compact=True)
    i.tooltip = _meta(sec, name).get("help", "")
    return Horizontal(Label(label, classes="flabel"), i, classes="frow wide" if wide else "frow")


def cselect(cfg, sec, name, label) -> Horizontal:
    meta = _meta(sec, name)
    ch = meta["choices"]
    cur = getattr(getattr(cfg, sec), name)
    s = Select([(c, c) for c in ch], value=cur if cur in ch else ch[0], allow_blank=False, id=f"cfg-{sec}__{name}", compact=True)
    s.tooltip = meta.get("help", "")
    return Horizontal(Label(label, classes="flabel"), s, classes="frow")


def cswitch(cfg, sec, name, label) -> Horizontal:
    s = Switch(value=bool(getattr(getattr(cfg, sec), name)), id=f"cfg-{sec}__{name}")
    s.tooltip = _meta(sec, name).get("help", "")
    return Horizontal(Label(label, classes="flabel"), s, classes="frow")


def quality(t) -> str:
    if t.lossless:
        return f"{t.bit_depth or '?'}bit/{t.sample_rate / 1000:g}k"
    return f"{t.bitrate}k"


def row_track_key(table: DataTable):
    if table.row_count == 0:
        return None
    return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value


class BasePane(VerticalScroll):
    DEFAULT_CLASSES = "pane"

    @property
    def cfg(self):
        return self.app.cfg

    @property
    def st(self):
        return self.app.state

    def refresh_state(self) -> None:
        pass

    def review(self, plan: Plan, title: str, *, workers: int = 1, mode: str = "", rescan: bool = True, after=None) -> None:
        def cb(result):
            self.app.state_changed()
            if result and not result.dry_run and rescan:
                self.app.scan_library(use_cache=True, done=after)
        self.app.push_screen(PlanReviewScreen(plan, title, workers=workers, mode=mode), cb)

    def hint(self, text: str) -> Static:
        return Static(text, classes="hint")


# ===================================================================== Library
class LibraryPane(BasePane):
    def compose(self) -> ComposeResult:
        yield self.hint("Folders that make up your library — a local folder or a device mount (your iPod works too). "
                        "Scan once to unlock the other tabs.")
        yield Horizontal(Input(placeholder="/path/to/music   (Enter adds it)", id="root-input"),
                         Button("Add folder", id="add-root", variant="primary"),
                         Button("Remove selected", id="rm-root"), classes="frow")
        yield DataTable(id="roots", cursor_type="row", classes="short")
        yield Horizontal(Button("▶ Scan", id="scan", variant="success"),
                         Button("Full rescan (ignore cache)", id="rescan"),
                         Button("■ Cancel", id="cancel", variant="error"), classes="buttons-row")
        yield Static("", id="lib-summary")
        yield Input(placeholder="🔎 filter by title / artist / album / path …", id="filter")
        yield DataTable(id="tracks", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#roots", DataTable).add_columns("Folder", "Exists")
        self.query_one("#tracks", DataTable).add_columns("Artist", "Title", "Album", "Fmt", "Quality", "Size", "Cover")
        self._roots()
        self.refresh_state()

    def _roots(self) -> None:
        t = self.query_one("#roots", DataTable)
        t.clear()
        for r in self.cfg.scan.roots:
            t.add_row(r, "✔" if os.path.isdir(os.path.expanduser(r)) else "[red]✖ missing[/red]", key=os.path.expanduser(r))

    def refresh_state(self) -> None:
        st = self.st
        s = self.query_one("#lib-summary", Static)
        if not st.tracks:
            s.update("[dim]Nothing scanned yet.[/dim]")
        else:
            lossless = sum(t.lossless for t in st.tracks)
            s.update(f"[b]{len(st.tracks):,}[/b] tracks · {short_size(sum(t.size for t in st.tracks))} · {lossless} lossless / "
                     f"{len(st.tracks) - lossless} lossy · {sum(not t.has_picture for t in st.tracks)} without cover"
                     + (f" · [red]{len(st.scan_errors)} unreadable[/red]" if st.scan_errors else ""))
        self._fill(self.query_one("#filter", Input).value)

    def _fill(self, flt: str) -> None:
        tb = self.query_one("#tracks", DataTable)
        tb.clear()
        f = flt.strip().casefold()
        n = 0
        for t in self.st.tracks:
            if f and f not in f"{t.title} {t.artist} {t.album} {t.path}".casefold():
                continue
            tb.add_row(t.artist, t.title or t.name, t.album, t.fmt, quality(t), short_size(t.size), "✔" if t.has_picture else "·", key=t.path)
            n += 1
            if n >= ROW_LIMIT:
                break

    def on_input_changed(self, e: Input.Changed) -> None:
        if e.input.id == "filter":
            self._fill(e.value)

    def on_input_submitted(self, e: Input.Submitted) -> None:
        if e.input.id == "root-input":
            self._add_root()

    def _add_root(self) -> None:
        i = self.query_one("#root-input", Input)
        p = os.path.abspath(os.path.expanduser(i.value.strip()))
        if not i.value.strip():
            return
        if not os.path.isdir(p):
            self.app.notify(f"Not a folder: {p}", severity="error")
            return
        if p not in self.cfg.scan.roots:
            self.cfg.scan.roots.append(p)
            self.app.save_cfg()
        i.value = ""
        self._roots()
        self.app.state_changed()

    def on_button_pressed(self, e: Button.Pressed) -> None:
        bid = e.button.id
        if bid == "add-root":
            self._add_root()
        elif bid == "rm-root":
            k = row_track_key(self.query_one("#roots", DataTable))
            self.cfg.scan.roots = [r for r in self.cfg.scan.roots if os.path.expanduser(r) != k]
            self.app.save_cfg()
            self._roots()
        elif bid == "scan":
            self.app.scan_library(True)
        elif bid == "rescan":
            self.app.scan_library(False)
        elif bid == "cancel":
            self.app.action_cancel_task()


# =================================================================== Duplicates
class DuplicatesPane(BasePane):
    def compose(self) -> ComposeResult:
        c = self.cfg
        yield self.hint("Groups copies of the same song and keeps the best one. Files with missing tags are NEVER auto-removed — "
                        "they land in the Review list so you can label or park them.")
        with Grid(classes="optgrid"):
            yield cselect(c, "dedupe", "action", "Action for losers")
        with Collapsible(title="Matching & ranking options", collapsed=True):
            with Grid(classes="optgrid"):
                yield cinput(c, "dedupe", "match_fields", "Tags that must match", wide=True)
                yield cswitch(c, "dedupe", "case_insensitive", "Ignore case")
                yield cswitch(c, "dedupe", "strip_punctuation", "Ignore punctuation")
                yield cswitch(c, "dedupe", "strip_brackets", "Ignore (brackets) text")
                yield cinput(c, "dedupe", "duration_tolerance_s", "Duration tolerance (s)")
                yield cswitch(c, "dedupe", "exact_hash", "Identical bytes = dup")
                yield cselect(c, "dedupe", "missing_policy", "Missing tags")
                yield cinput(c, "dedupe", "criteria", "Keeper ranking", wide=True)
                yield cinput(c, "dedupe", "tie_tolerance_pct", "Tie tolerance (%)")
                yield cinput(c, "dedupe", "preferred_paths", "Preferred paths", wide=True)
        yield Horizontal(Button("▶ Find duplicates", id="find", variant="success"),
                         Button("Plan & apply…", id="apply", variant="primary", disabled=True), classes="buttons-row")
        yield Static("", id="dup-summary")
        yield DataTable(id="groups", cursor_type="row", zebra_stripes=True)
        yield Static("[b]Needs review[/b] — missing / unknown tags", id="review-title")
        yield DataTable(id="review", cursor_type="row", zebra_stripes=True, classes="short")
        yield Horizontal(Button("Label tags…", id="edit-tags"), Button("Move selected → review folder", id="park-one"),
                         Button("Move ALL → review folder", id="park-all"), classes="buttons-row")

    def on_mount(self) -> None:
        self.query_one("#groups", DataTable).add_columns("Group", "", "File", "Fmt", "Quality", "Cover", "Size", "Why")
        self.query_one("#review", DataTable).add_columns("File", "Title", "Artist", "Album", "Why")
        self.refresh_state()

    def refresh_state(self) -> None:
        d = self.st.dupes
        g, r = self.query_one("#groups", DataTable), self.query_one("#review", DataTable)
        g.clear()
        r.clear()
        self.query_one("#apply", Button).disabled = d is None or not d.groups
        s = self.query_one("#dup-summary", Static)
        if d is None:
            s.update("[dim]Press “Find duplicates”.[/dim]")
            return
        s.update(f"[b]{len(d.groups)}[/b] duplicate groups · [b]{d.loser_count}[/b] copies to remove "
                 f"([b]{short_size(d.loser_bytes)}[/b] freed) · {d.unique} unique · [yellow]{len(d.review)} need review[/yellow]")
        n = 0
        for grp in d.groups:
            for i, t in enumerate([grp.keeper] + grp.losers):
                keep = t is grp.keeper
                g.add_row(grp.key[:26] if i == 0 else "", Text("KEEP", style="bold green") if keep else Text("remove", style="red"),
                          t.name, t.fmt, quality(t), "✔" if t.has_picture else "·", short_size(t.size),
                          "; ".join(grp.reasons)[:60] if keep else "", key=t.path)
                n += 1
            if n > ROW_LIMIT * 2:
                break
        for t, why in d.review[:ROW_LIMIT]:
            r.add_row(t.name, t.title, t.artist, t.album, why, key=t.path)

    def find(self) -> None:
        tracks = self.st.tracks

        def job(prog, cancel):
            return dd.find_duplicates(tracks, self.cfg, progress=prog)

        def done(res):
            self.st.dupes = res
            self.app.state_changed()

        self.app.bg(job, label="Finding duplicates", done=done)

    def on_button_pressed(self, e: Button.Pressed) -> None:
        bid = e.button.id
        if bid == "find":
            self.find()
        elif bid == "apply":
            if self.cfg.dedupe.action == "report":
                self.app.notify("Action is “report” — pick bin / quarantine / delete above to act.", severity="warning")
                return
            self.review(dd.build_plan(self.st.dupes, self.cfg), f"Remove duplicates → {self.cfg.dedupe.action}", after=self.find)
        elif bid == "edit-tags":
            key = row_track_key(self.query_one("#review", DataTable))
            t = self.st.by_path.get(key) if key else None
            if t:
                self.app.push_screen(TagEditModal(t), lambda v, t=t: self._save_tags(t, v))
        elif bid in ("park-one", "park-all") and self.st.dupes:
            if bid == "park-one":
                key = row_track_key(self.query_one("#review", DataTable))
                items = [t for t, _ in self.st.dupes.review if t.path == key]
            else:
                items = [t for t, _ in self.st.dupes.review]
            if items:
                self.review(dd.review_dir_plan(items, self.cfg), "Park unlabelled files", after=self.find)

    def _save_tags(self, t, values) -> None:
        if not values:
            return
        vals = {k: v for k, v in values.items() if v and v != getattr(t, k)}
        if not vals:
            return
        plan = Plan("tag", [Action("tag", src=t.path, params={"values": vals}, note="manual label")])

        def job(prog, cancel):
            return Executor(self.cfg).run(plan, "tag")

        self.app.bg(job, label=f"Label {t.name}", done=lambda r: self.app.scan_library(True, done=self.find))


# ===================================================================== Artwork
class ArtworkPane(BasePane):
    def compose(self) -> ComposeResult:
        c = self.cfg
        yield self.hint("Finds tracks without cover art and fills them from a duplicate, another track of the same album, or a cover.jpg in the folder.")
        with Grid(classes="optgrid"):
            yield cinput(c, "artwork", "donor_order", "Donor order", wide=True)
            yield cinput(c, "artwork", "folder_image_names", "Folder image names", wide=True)
            yield cinput(c, "artwork", "min_resolution", "Min. cover size (px)")
            yield cswitch(c, "artwork", "replace_lower_res", "Replace small covers")
            yield cswitch(c, "artwork", "extract_to_folder", "Save cover.jpg in folders")
        yield Horizontal(Button("▶ Analyze", id="analyze", variant="success"),
                         Button("Plan & apply…", id="apply", variant="primary", disabled=True), classes="buttons-row")
        yield Static("", id="art-summary")
        yield DataTable(id="fixes", cursor_type="row", zebra_stripes=True)
        yield Static("[b]No cover found anywhere[/b]", id="nodonor-title")
        yield DataTable(id="nodonor", cursor_type="row", classes="short")

    def on_mount(self) -> None:
        self.query_one("#fixes", DataTable).add_columns("Track", "Source", "From", "Detail")
        self.query_one("#nodonor", DataTable).add_columns("Track", "Album", "Artist")
        self.refresh_state()

    def refresh_state(self) -> None:
        a = self.st.art
        f, n = self.query_one("#fixes", DataTable), self.query_one("#nodonor", DataTable)
        f.clear()
        n.clear()
        self.query_one("#apply", Button).disabled = a is None or not (a.fixes or self.cfg.artwork.extract_to_folder)
        s = self.query_one("#art-summary", Static)
        if a is None:
            s.update("[dim]Press “Analyze”.[/dim]")
            return
        s.update(f"[b]{len(a.fixes)}[/b] fixable · [yellow]{len(a.no_donor)}[/yellow] no donor · {len(a.unsupported)} unsupported format · {a.ok} already fine")
        for t, kind, donor, detail in a.fixes[:ROW_LIMIT]:
            f.add_row(t.name, kind, os.path.basename(donor), detail, key=t.path)
        for t in a.no_donor[:ROW_LIMIT]:
            n.add_row(t.name, t.album, t.artist, key=t.path)

    def on_button_pressed(self, e: Button.Pressed) -> None:
        if e.button.id == "analyze":
            def job(prog, cancel):
                return art.find_art_fixes(self.st.tracks, self.cfg, self.st.dupes)

            def done(res):
                self.st.art = res
                self.app.state_changed()
            self.app.bg(job, label="Analyzing artwork", done=done)
        elif e.button.id == "apply" and self.st.art:
            plan = art.build_plan(self.st.art, self.st.tracks, self.cfg)
            self.review(plan, "Fix artwork", after=None)


# ==================================================================== Organize
class OrganizePane(BasePane):
    def compose(self) -> ComposeResult:
        c = self.cfg
        yield self.hint("Moves files into Artist/Album folders. Choose hardlink / symlink mode to PREVIEW the new layout without "
                        "touching your originals, then Finalize (keep new) or Discard (keep old).")
        with Grid(classes="optgrid"):
            yield cinput(c, "organize", "dest_root", "Destination folder", "/music/organized  (can be your iPod)", wide=True)
            yield cinput(c, "organize", "template", "Folder/file template", wide=True)
            yield cselect(c, "organize", "mode", "Mode")
            yield cselect(c, "organize", "collision", "If file exists")
            yield cselect(c, "organize", "main_artist_rule", "Artist folder rule")
            yield cselect(c, "organize", "unknown_policy", "Unknown-tag files")
            yield cselect(c, "organize", "sanitize_profile", "Name sanitizing")
        with Collapsible(title="More options", collapsed=True):
            with Grid(classes="optgrid"):
                yield cinput(c, "organize", "replace_char", "Replace bad chars with")
                yield cinput(c, "organize", "required_tags", "Tags required (else unknown)")
                yield cinput(c, "organize", "unsorted_dir", "Unsorted folder")
                yield cswitch(c, "organize", "unify_case_variants", "Merge case variants")
                yield cinput(c, "organize", "dominant_share", "Dominant artist share")
                yield cinput(c, "organize", "compilation_min_artists", "Compilation if ≥ N artists")
                yield cinput(c, "organize", "compilation_name", "Compilation folder")
                yield cselect(c, "organize", "case_mode", "Name case")
                yield cselect(c, "organize", "unicode_norm", "Unicode form")
                yield cinput(c, "organize", "max_component_len", "Max name length")
                yield cinput(c, "organize", "max_path_len", "Max path length")
                yield cselect(c, "organize", "link_fallback", "Hardlink fallback")
                yield cselect(c, "organize", "finalize_old", "Finalize copies: old files →")
                yield cswitch(c, "organize", "move_sidecars", "Move .lrc/.cue sidecars")
                yield cswitch(c, "organize", "remove_empty_dirs", "Remove emptied folders")
        yield Horizontal(Button("▶ Preview plan", id="preview", variant="success"),
                         Button("Apply…", id="apply", variant="primary", disabled=True), classes="buttons-row")
        yield Static("", id="org-summary")
        yield DataTable(id="org-table", cursor_type="row", zebra_stripes=True)
        yield Static("[b]History & previews[/b]", id="hist-title")
        yield DataTable(id="history", cursor_type="row", classes="short")
        yield Horizontal(Button("Finalize preview", id="finalize", variant="success"), Button("Discard preview", id="discard", variant="warning"),
                         Button("Undo selected", id="undo"), classes="buttons-row")

    def on_mount(self) -> None:
        self.query_one("#org-table", DataTable).add_columns("Op", "Source", "→ Destination", "Note")
        self.query_one("#history", DataTable).add_columns("When", "Kind", "Mode", "State", "Entries")
        self.refresh_state()

    def refresh_state(self) -> None:
        h = self.query_one("#history", DataTable)
        h.clear()
        try:
            for j in jr.list_journals(self.cfg)[:200]:
                m = j.meta
                state = {"applied": "[cyan]applied[/cyan]", "finalized": "finalized", "discarded": "discarded", "undone": "undone"}.get(m["state"], m["state"])
                if j.is_preview():
                    state = "[b cyan]PREVIEW (pending)[/b cyan]"
                h.add_row(time.strftime("%m-%d %H:%M", time.localtime(m["created"])), m["kind"], m["mode"], state, str(m["count"]), key=m["id"])
        except Exception:
            pass
        if self.st.org_plan is None:
            self.query_one("#org-table", DataTable).clear()
            self.query_one("#apply", Button).disabled = True
            self.query_one("#org-summary", Static).update("[dim]Press “Preview plan” — nothing changes until you Apply.[/dim]")

    def _show_plan(self, plan, rep) -> None:
        self.st.org_plan = plan
        t = self.query_one("#org-table", DataTable)
        t.clear()
        root = plan.meta["dest_root"]
        for a in plan.actions[:ROW_LIMIT]:
            if a.op != "cleanup":
                t.add_row(a.op, short(a.src, 50), short(os.path.relpath(a.dst, root), 60), a.note)
        warn = f" · [yellow]{rep.unsorted} unknown → unsorted[/yellow]" if rep.unsorted else ""
        warn += f" · {rep.collisions} collisions" if rep.collisions else ""
        warn += f" · {rep.compilations} compilations" if rep.compilations else ""
        warn += f" · [yellow]{len(rep.split_albums)} same-name albums split by artist[/yellow]" if rep.split_albums else ""
        n = len([a for a in plan.actions if a.op != 'cleanup'])
        self.query_one("#org-summary", Static).update(
            f"[b]{n}[/b] files → {root} · {rep.in_place} already in place · {rep.skipped} skipped{warn}")
        self.query_one("#apply", Button).disabled = n == 0

    def on_button_pressed(self, e: Button.Pressed) -> None:
        bid = e.button.id
        if bid == "preview":
            def job(prog, cancel):
                return org.build_plan(self.st.tracks, self.cfg)

            def done(res):
                self._show_plan(*res)
            try:
                self.app.bg(job, label="Building organize plan", done=done)
            except ValueError as ex:
                self.app.notify(str(ex), severity="error")
        elif bid == "apply" and self.st.org_plan:
            self.review(self.st.org_plan, f"Organize ({self.cfg.organize.mode})", mode=self.cfg.organize.mode,
                        after=lambda: setattr(self.st, "org_plan", None))
        elif bid in ("finalize", "discard", "undo"):
            self._journal_action(bid)

    def _journal_action(self, which: str) -> None:
        key = row_track_key(self.query_one("#history", DataTable))
        if not key:
            return
        j = jr.Journal(self.cfg, "", jid=key)
        if which in ("finalize", "discard") and not j.is_preview():
            self.app.notify("That entry is not a pending preview.", severity="warning")
            return
        if which == "undo" and j.state not in ("applied",):
            self.app.notify(f"Cannot undo: state is {j.state}.", severity="warning")
            return
        fn = {"finalize": jr.finalize, "discard": jr.discard, "undo": jr.undo}[which]
        text = {"finalize": "Finalize: the NEW layout becomes permanent and the OLD names/files go away.",
                "discard": "Discard: remove the preview. Your original files are untouched.",
                "undo": "Undo: reverse everything this run did."}[which]

        def go(ok):
            if not ok:
                return

            def done(out):
                msg = f"{which}: {out.done} done, {out.skipped} skipped, {len(out.errors)} errors, {out.irreversible} irreversible"
                self.app.log_line(msg, "warn" if out.errors else "info")
                self.app.notify(msg)
                self.app.scan_library(True)
            self.app.bg(lambda p, c: fn(self.cfg, j), label=which.capitalize() + " " + key, done=done)
        self.app.push_screen(ConfirmModal(text, which.capitalize(), danger=(which == "finalize")), go)


# ======================================================================== Sync
class SyncPane(BasePane):
    def compose(self) -> ComposeResult:
        c = self.cfg
        yield self.hint("Prepares a copy for a device (iPod/phone). Rules decide per file: copy, link, convert (e.g. shrink huge hi-res files) or skip. "
                        "Point 'Target' at the device, or work locally first.")
        with Grid(classes="optgrid"):
            yield cinput(c, "convert", "target_root", "Target folder / device", "/media/you/IPOD/Music", wide=True)
            yield cselect(c, "convert", "layout", "Layout")
            yield cselect(c, "convert", "default_action", "If no rule matches")
            yield cselect(c, "convert", "link_mode", "'link' means")
            yield cswitch(c, "convert", "incremental", "Skip files already synced")
            yield cswitch(c, "convert", "delete_orphans", "Remove orphans on device")
            yield cswitch(c, "convert", "no_upsample", "Never upsample")
            yield cswitch(c, "convert", "dither", "Dither on bit reduction")
            yield cinput(c, "convert", "jobs", "Parallel ffmpeg jobs")
            yield cinput(c, "convert", "ffmpeg_path", "ffmpeg path")
        yield Static("[b]Rules[/b]  (first enabled matching rule wins)", classes="h")
        yield DataTable(id="rules", cursor_type="row", classes="short")
        yield Horizontal(Button("Add rule", id="r-add"), Button("Edit", id="r-edit"), Button("Delete", id="r-del"),
                         Button("▲", id="r-up"), Button("▼", id="r-down"), classes="buttons-row")
        yield Horizontal(Button("▶ Preview sync", id="preview", variant="success"),
                         Button("Apply…", id="apply", variant="primary", disabled=True), classes="buttons-row")
        yield Static("", id="sync-summary")
        yield DataTable(id="sync-table", cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#rules", DataTable).add_columns("#", "On", "Name", "If", "Then")
        self.query_one("#sync-table", DataTable).add_columns("Op", "Source", "→ Target", "Note")
        self._rules()

    def _rules(self) -> None:
        t = self.query_one("#rules", DataTable)
        t.clear()
        for i, r in enumerate(self.cfg.convert.rules):
            conds = []
            if r.get("min_size_mib"):
                conds.append(f"size ≥ {r['min_size_mib']:g} MiB")
            if r.get("max_size_mib"):
                conds.append(f"size ≤ {r['max_size_mib']:g} MiB")
            if r.get("min_sample_rate"):
                conds.append(f"rate ≥ {r['min_sample_rate']}")
            if r.get("min_bit_depth"):
                conds.append(f"bits ≥ {r['min_bit_depth']}")
            if r.get("formats"):
                conds.append(f"fmt in {r['formats']}")
            then = r.get("action", "copy")
            if then == "convert":
                then += f" → {r.get('codec')} " + "/".join(str(x) for x in (r.get("bit_depth") or "", r.get("sample_rate") or "") if x != "")
            t.add_row(str(i + 1), "✔" if r.get("enabled", True) else "·", r.get("name", ""), " & ".join(conds) or "always", then, key=f"rule{i}")

    def refresh_state(self) -> None:
        if self.st.sync_plan is None:
            self.query_one("#sync-table", DataTable).clear()
            self.query_one("#apply", Button).disabled = True
            self.query_one("#sync-summary", Static).update("[dim]Press “Preview sync”.[/dim]")

    def _sel(self) -> int | None:
        k = row_track_key(self.query_one("#rules", DataTable))
        return int(k[4:]) if k else None

    def on_button_pressed(self, e: Button.Pressed) -> None:
        bid = e.button.id
        rules = self.cfg.convert.rules
        i = self._sel()
        if bid == "r-add":
            self.app.push_screen(RuleEditModal(dict(cv.NEW_RULE)), self._after_edit(None))
        elif bid == "r-edit" and i is not None:
            self.app.push_screen(RuleEditModal(rules[i]), self._after_edit(i))
        elif bid == "r-del" and i is not None:
            rules.pop(i)
            self._save()
        elif bid in ("r-up", "r-down") and i is not None:
            j = i - 1 if bid == "r-up" else i + 1
            if 0 <= j < len(rules):
                rules[i], rules[j] = rules[j], rules[i]
                self._save()
                self.query_one("#rules", DataTable).move_cursor(row=j)
        elif bid == "preview":
            def job(prog, cancel):
                return cv.build_plan(self.st.tracks, self.cfg)
            self.app.bg(job, label="Building sync plan", done=lambda res: self._show(*res))
        elif bid == "apply" and self.st.sync_plan:
            self.review(self.st.sync_plan, "Convert & sync", workers=max(1, self.cfg.convert.jobs), mode="sync", rescan=False)

    def _after_edit(self, idx):
        def cb(rule):
            if rule is None:
                return
            if idx is None:
                self.cfg.convert.rules.append(rule)
            else:
                self.cfg.convert.rules[idx] = rule
            self._save()
        return cb

    def _save(self) -> None:
        self.app.save_cfg()
        self._rules()

    def _show(self, plan, rep) -> None:
        self.st.sync_plan = plan
        t = self.query_one("#sync-table", DataTable)
        t.clear()
        root = plan.meta["target_root"]
        for a in plan.actions[:ROW_LIMIT]:
            t.add_row(a.op, short(a.src, 50), short(os.path.relpath(a.dst, root) if a.dst else "", 55), a.note)
        by_rule = ", ".join(f"{k}: {v}" for k, v in rep.by_rule.items())
        self.query_one("#sync-summary", Static).update(
            f"copy [b]{rep.copy}[/b] · link [b]{rep.link}[/b] · convert [b]{rep.convert}[/b] ({short_size(rep.est_src_bytes)} source) · "
            f"skip {rep.skip} · already up to date {rep.up_to_date}"
            + (f" · [yellow]{rep.orphans} orphans[/yellow]" if rep.orphans else "") + f"\n[dim]matched by rule → {by_rule}[/dim]")
        self.query_one("#apply", Button).disabled = not plan.actions


# ===================================================================== Snapshot
class SnapshotPane(BasePane):
    def compose(self) -> ComposeResult:
        c = self.cfg
        snaps = str(c.sub_dir("snapshots") / f"snapshot-{time.strftime('%Y%m%d-%H%M%S')}.json")
        yield self.hint("Save the current folder structure to JSON, and later rebuild that structure from it — on this computer or on your device.")
        yield Static("[b]Export snapshot[/b]", classes="h")
        yield Horizontal(Label("Folder", classes="flabel"), Input(value=(c.organize.dest_root or (c.scan.roots[0] if c.scan.roots else "")), id="snap-root"), classes="frow")
        yield Horizontal(Label("Save as", classes="flabel"), Input(value=snaps, id="snap-out"), classes="frow")
        yield Horizontal(Button("Export snapshot", id="export", variant="success"), classes="buttons-row")
        yield Static("[b]Restore / re-organize from snapshot[/b]", classes="h")
        yield Horizontal(Label("Snapshot file", classes="flabel"), Input(placeholder="snapshot.json (new or old format)", id="snap-file"), classes="frow")
        yield Horizontal(Label("Files are currently in", classes="flabel"), Input(id="snap-src"), classes="frow")
        yield Horizontal(Label("Rebuild into", classes="flabel"), Input(id="snap-dst"), classes="frow")
        yield Horizontal(Label("Mode", classes="flabel"),
                         Select([(m, m) for m in ("move", "copy", "hardlink", "symlink")], value="move", allow_blank=False, id="snap-mode"), classes="frow")
        yield Horizontal(Button("▶ Preview restore", id="r-preview", variant="success"),
                         Button("Apply…", id="r-apply", variant="primary", disabled=True), classes="buttons-row")
        yield Static("", id="snap-summary")

    def on_button_pressed(self, e: Button.Pressed) -> None:
        q = lambda i: self.query_one(i, Input).value.strip()
        if e.button.id == "export":
            root, out = q("#snap-root"), q("#snap-out")
            if not os.path.isdir(os.path.expanduser(root)):
                self.app.notify("Folder does not exist", severity="error")
                return
            exts = self.cfg.scan.formats

            def done(d):
                n = sum(len(s) for a in d["structure"].values() for s in a.values())
                self.query_one("#snap-summary", Static).update(f"[green]Saved[/green] {n} files in {len(d['structure'])} artists → {out}")
                self.query_one("#snap-file", Input).value = out
            self.app.bg(lambda p, c: snapshot.export_structure(root, out, exts), label="Exporting snapshot", done=done)
        elif e.button.id == "r-preview":
            f, src, dst = q("#snap-file"), q("#snap-src"), q("#snap-dst")
            if not (os.path.isfile(f) and os.path.isdir(os.path.expanduser(src)) and dst):
                self.app.notify("Need an existing snapshot file, source folder and a destination", severity="error")
                return
            mode = self.query_one("#snap-mode", Select).value

            def job(prog, cancel):
                return snapshot.restore_plan(snapshot.load_snapshot(f), src, dst, self.cfg, mode)

            def done(res):
                plan, rep = res
                self.st.restore_plan = plan
                miss = f" · [red]{len(rep.missing)} not found[/red] (e.g. {', '.join(rep.missing[:2])})" if rep.missing else ""
                self.query_one("#snap-summary", Static).update(
                    f"{rep.found}/{rep.wanted} files found · {len(plan.actions)} to {mode}{miss}"
                    + (f" · {rep.ambiguous} ambiguous names" if rep.ambiguous else ""))
                self.query_one("#r-apply", Button).disabled = not plan.actions
            self.app.bg(job, label="Building restore plan", done=done)
        elif e.button.id == "r-apply" and self.st.restore_plan:
            self.review(self.st.restore_plan, "Restore layout", mode=self.st.restore_plan.meta.get("mode", ""))


# ====================================================================== Reports
class ReportsPane(BasePane):
    def compose(self) -> ComposeResult:
        yield self.hint("Library statistics. Sizes are shown in MiB, GiB and GB like your old FileSize script.")
        yield Horizontal(Button("↻ Refresh", id="refresh", variant="success"), Button("Export library JSON", id="exp-json"),
                         Button("Export library CSV", id="exp-csv"), classes="buttons-row")
        yield Static("", id="report")

    def on_mount(self) -> None:
        self.refresh_state()

    def refresh_state(self) -> None:
        st = self.st
        w = self.query_one("#report", Static)
        if not st.tracks:
            w.update("[dim]Scan the library first.[/dim]")
            return
        s = reports.library_stats(st.tracks, self.cfg)
        g = Table.grid(padding=(0, 2))
        g.add_row("[b]Tracks[/b]", f"{s['tracks']:,}  ({s['lossless']:,} lossless / {s['lossy']:,} lossy) · {s['duration_hours']} h")
        g.add_row("[b]Total size[/b]", s["total_size"])
        if s["files_over_rule_threshold"]:
            g.add_row("[b]Over first rule size[/b]", f"{s['files_over_rule_threshold']} files")
        f = Table("Format", "Files", "Size", box=None, pad_edge=False)
        for k, v in sorted(s["by_format"].items(), key=lambda kv: -kv[1]["bytes"]):
            f.add_row(k, f"{v['files']:,}", short_size(v["bytes"]))
        m = Table("Missing", "Count", box=None, pad_edge=False)
        for k, v in s["missing_tags"].items():
            m.add_row(k, f"[red]{v}[/red]" if v else "0")
        a = Table("Top artists", "Tracks", box=None, pad_edge=False)
        for k, v in s["top_artists"]:
            a.add_row(k[:40], str(v))
        b = Table("Largest files", "MiB", box=None, pad_edge=False)
        for k, v in s["largest"]:
            b.add_row(k[:44], f"{v:,}")
        out = Table.grid(padding=(1, 4))
        out.add_row(f, m)
        out.add_row(a, b)
        from rich.console import Group
        w.update(Group(g, Text(""), out))

    def on_button_pressed(self, e: Button.Pressed) -> None:
        if e.button.id == "refresh":
            self.refresh_state()
        elif e.button.id in ("exp-json", "exp-csv") and self.st.tracks:
            ext = "json" if e.button.id == "exp-json" else "csv"
            p = reports.export_tracks(self.st.tracks, str(self.cfg.sub_dir("reports") / f"library-{time.strftime('%Y%m%d-%H%M%S')}.{ext}"))
            self.app.notify(f"Saved {p}")
            self.app.log_line(f"exported library → {p}")
