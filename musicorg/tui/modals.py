from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, ProgressBar, Select, Static, Switch

from ..core.executor import Executor, RunResult
from ..core.plan import Plan
from ..core.util import short_size

MAX_ROWS = 3000


def short(path: str, n: int = 60) -> str:
    return path if len(path) <= n else "…" + path[-(n - 1):]


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "no", "No")]

    def __init__(self, text: str, yes: str = "Yes", danger: bool = False):
        super().__init__()
        self.text, self.yes, self.danger = text, yes, danger

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog small"):
            yield Static(self.text, classes="dialog-text")
            with Horizontal(classes="buttons"):
                yield Button(self.yes, id="yes", variant="error" if self.danger else "primary")
                yield Button("Cancel", id="no")

    def on_button_pressed(self, e: Button.Pressed) -> None:
        self.dismiss(e.button.id == "yes")

    def action_no(self) -> None:
        self.dismiss(False)


class TagEditModal(ModalScreen):
    """Label a track whose tags are missing."""
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, track):
        super().__init__()
        self.track = track

    def compose(self) -> ComposeResult:
        t = self.track
        with Vertical(classes="dialog"):
            yield Static(f"[b]Label tags[/b]\n{short(t.path, 70)}", classes="dialog-text")
            for key, val in (("title", t.title), ("artist", t.artist), ("album", t.album), ("albumartist", t.albumartist)):
                with Horizontal(classes="frow"):
                    yield Label(key, classes="flabel")
                    yield Input(value=val, id=f"tag-{key}")
            with Horizontal(classes="buttons"):
                yield Button("Save to file", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, e: Button.Pressed) -> None:
        if e.button.id == "save":
            self.dismiss({k: self.query_one(f"#tag-{k}", Input).value.strip()
                          for k in ("title", "artist", "album", "albumartist")})
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RuleEditModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    TEXT = [("name", "Name"), ("min_size_mib", "File size ≥ (MiB, 0=off)"), ("max_size_mib", "File size ≤ (MiB, 0=off)"),
            ("min_sample_rate", "Sample rate ≥ (Hz, 0=off)"), ("min_bit_depth", "Bit depth ≥ (0=off)"),
            ("formats", "Only formats (comma list, empty=all)")]
    OUT = [("sample_rate", "→ sample rate (Hz, 0=keep)"), ("bit_depth", "→ bit depth (0=keep)"),
           ("channels", "→ channels (0=keep)"), ("bitrate_kbps", "→ bitrate kbps (lossy, 0=auto)")]

    def __init__(self, rule: dict):
        super().__init__()
        self.rule = dict(rule)

    def compose(self) -> ComposeResult:
        from ..core.convert import CODECS
        r = self.rule
        with VerticalScroll(classes="dialog"):
            yield Static("[b]Sync rule[/b]  (first enabled matching rule wins)", classes="dialog-text")
            with Horizontal(classes="frow"):
                yield Label("Enabled", classes="flabel")
                yield Switch(value=bool(r.get("enabled", True)), id="r-enabled")
            for key, label in self.TEXT:
                with Horizontal(classes="frow"):
                    yield Label(label, classes="flabel")
                    yield Input(value=str(r.get(key, "")), id=f"r-{key}")
            with Horizontal(classes="frow"):
                yield Label("THEN action", classes="flabel")
                yield Select([(a, a) for a in ("copy", "link", "convert", "skip")], value=r.get("action", "copy"),
                             allow_blank=False, id="r-action")
            with Horizontal(classes="frow"):
                yield Label("→ codec", classes="flabel")
                yield Select([(c, c) for c in CODECS], value=r.get("codec", "flac"), allow_blank=False, id="r-codec")
            for key, label in self.OUT:
                with Horizontal(classes="frow"):
                    yield Label(label, classes="flabel")
                    yield Input(value=str(r.get(key, 0)), id=f"r-{key}")
            with Horizontal(classes="buttons"):
                yield Button("Save rule", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, e: Button.Pressed) -> None:
        if e.button.id != "save":
            self.dismiss(None)
            return
        out = dict(self.rule)
        out["enabled"] = self.query_one("#r-enabled", Switch).value
        for key, _ in self.TEXT + self.OUT:
            raw = self.query_one(f"#r-{key}", Input).value.strip()
            cur = self.rule.get(key, "")
            try:
                out[key] = float(raw or 0) if isinstance(cur, float) else int(float(raw or 0)) if isinstance(cur, int) else raw
            except ValueError:
                out[key] = cur
        out["action"] = self.query_one("#r-action", Select).value
        out["codec"] = self.query_one("#r-codec", Select).value
        self.dismiss(out)

    def action_cancel(self) -> None:
        self.dismiss(None)


class PlanReviewScreen(ModalScreen):
    """Shows what WILL happen. Space toggles a row, then Apply."""
    BINDINGS = [Binding("escape", "close", "Close"), Binding("space", "toggle_row", "Toggle row"),
                Binding("a", "all_on", "All on"), Binding("n", "all_off", "All off")]

    def __init__(self, plan: Plan, title: str, *, workers: int = 1, mode: str = ""):
        super().__init__()
        self.plan, self.title_text, self.n_workers, self.run_mode = plan, title, workers, mode
        self.run_result: RunResult | None = None
        self.exec_running = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Static(f"[b]{self.title_text}[/b]", classes="dialog-text")
            yield Static("", id="plan-summary", classes="dialog-text")
            yield DataTable(id="plan-table", cursor_type="row", zebra_stripes=True)
            yield ProgressBar(id="plan-progress", show_eta=False)
            yield Static("", id="plan-result", classes="dialog-text")
            with Horizontal(classes="buttons"):
                yield Button("Apply", id="apply", variant="primary")
                yield Button("Simulate (dry-run)", id="dry")
                yield Button("Close", id="close")

    def on_mount(self) -> None:
        t = self.query_one("#plan-table", DataTable)
        t.add_columns("✔", "Operation", "Source", "→ Destination", "Note")
        for i, a in enumerate(self.plan.actions[:MAX_ROWS]):
            t.add_row("✔" if a.enabled else " ", a.op, short(a.src, 48), short(a.dst, 52), a.note, key=str(i))
        self.query_one("#plan-progress", ProgressBar).display = False
        self._summary()
        self.app.progress_listeners.append(self._on_progress)
        if not self.plan.actions:
            self.query_one("#apply", Button).disabled = True
            self.query_one("#dry", Button).disabled = True

    def on_unmount(self) -> None:
        if self._on_progress in self.app.progress_listeners:
            self.app.progress_listeners.remove(self._on_progress)

    def _summary(self) -> None:
        acts = self.plan.actions
        on = sum(a.enabled for a in acts)
        ops: dict[str, int] = {}
        for a in acts:
            if a.enabled:
                ops[a.op] = ops.get(a.op, 0) + 1
        extra = f"  (showing first {MAX_ROWS})" if len(acts) > MAX_ROWS else ""
        live = "[b red]DRY-RUN is ON in settings[/b red]  " if self.app.cfg.general.dry_run else ""
        self.query_one("#plan-summary", Static).update(
            f"{live}{on}/{len(acts)} actions enabled: " + ", ".join(f"{k} ×{v}" for k, v in ops.items()) + extra
            + "\n[dim]Space = toggle row · A = all on · N = all off · nothing happens until you press Apply[/dim]")

    def action_toggle_row(self) -> None:
        if self.exec_running:
            return
        t = self.query_one("#plan-table", DataTable)
        if t.row_count == 0:
            return
        key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key
        a = self.plan.actions[int(key.value)]
        a.enabled = not a.enabled
        t.update_cell(key, t.ordered_columns[0].key, "✔" if a.enabled else " ")
        self._summary()

    def _set_all(self, v: bool) -> None:
        if self.exec_running:
            return
        t = self.query_one("#plan-table", DataTable)
        for a in self.plan.actions:
            a.enabled = v
        for rk in list(t.rows):
            t.update_cell(rk, t.ordered_columns[0].key, "✔" if v else " ")
        self._summary()

    def action_all_on(self) -> None:
        self._set_all(True)

    def action_all_off(self) -> None:
        self._set_all(False)

    def action_close(self) -> None:
        if not self.exec_running:
            self.dismiss(self.run_result)

    def on_button_pressed(self, e: Button.Pressed) -> None:
        if e.button.id == "close":
            self.action_close()
        elif e.button.id in ("apply", "dry") and not self.exec_running:
            dry = e.button.id == "dry"
            if not dry and any(a.enabled and a.op == "delete" for a in self.plan.actions) and self.app.cfg.trash.confirm_permanent:
                n = sum(a.enabled and a.op == "delete" for a in self.plan.actions)
                self.app.push_screen(ConfirmModal(f"{n} files will be deleted [b]permanently[/b] (not recoverable).", "Delete permanently", True),
                                     lambda ok: ok and self._run(False))
            else:
                self._run(dry)

    def _run(self, dry: bool) -> None:
        self.exec_running = True
        for b in ("apply", "dry", "close"):
            self.query_one(f"#{b}", Button).disabled = True
        bar = self.query_one("#plan-progress", ProgressBar)
        bar.display = True
        bar.update(total=max(1, len(self.plan.active())), progress=0)

        def job(prog, cancel):
            return Executor(self.app.cfg, dry_run=dry, progress=prog, cancel=cancel, workers=self.n_workers).run(self.plan, self.run_mode)

        def done(res: RunResult):
            self.exec_running, self.run_result = False, res
            self.query_one("#close", Button).disabled = False
            lines = [f"[b]{'Simulated' if res.dry_run else 'Done'}[/b]: {res.done} ok, {res.skipped} skipped, "
                     f"{len(res.failed)} failed · {short_size(res.bytes_done)}"]
            if res.cancelled:
                lines.append("[yellow]cancelled[/yellow]")
            for f in res.failed[:5]:
                lines.append(f"[red]✖ {f}[/red]")
            if res.journal_id:
                lines.append(f"[dim]journal {res.journal_id} (Organize tab → History can undo)[/dim]")
            self.query_one("#plan-result", Static).update("\n".join(lines))
            self.app.log_line(f"{self.title_text}: {res.done} ok / {res.skipped} skipped / {len(res.failed)} failed" + (" (dry-run)" if res.dry_run else ""),
                              "warn" if res.failed else "info")

        self.app.bg(job, label=self.title_text + (" (dry-run)" if dry else ""), done=done)

    def _on_progress(self, d: int, t: int, msg: str) -> None:
        if t:
            self.query_one("#plan-progress", ProgressBar).update(total=t, progress=d)
