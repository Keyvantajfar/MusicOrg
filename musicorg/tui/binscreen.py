from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Static

from ..core.util import short_size
from .modals import ConfirmModal, short


class BinScreen(Screen):
    BINDINGS = [Binding("escape", "close", "Back"), Binding("r", "restore", "Restore"), Binding("delete", "purge", "Delete permanently")]

    def compose(self) -> ComposeResult:
        yield Static("[b]🗑 Recycle bin[/b]   [dim]nothing here is gone until you delete it permanently[/dim]", id="bin-top")
        yield Static("", id="bin-summary")
        yield DataTable(id="bin-table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="bin-buttons"):
            yield Button("↩ Restore selected", id="restore", variant="success")
            yield Button("✖ Delete selected permanently", id="purge", variant="error")
            yield Button("Empty the bin", id="empty", variant="error")
            yield Button("Back", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#bin-table", DataTable).add_columns("File", "Original location", "Deleted", "Size", "Why")
        self.reload()

    def reload(self) -> None:
        b = self.app.bin()
        t = self.query_one("#bin-table", DataTable)
        t.clear()
        items = sorted(b.list(), key=lambda i: -i.deleted_at)
        for i in items:
            t.add_row(i.name, short(i.original, 60), time.strftime("%Y-%m-%d %H:%M", time.localtime(i.deleted_at)),
                      short_size(i.size), i.reason, key=i.id)
        self.query_one("#bin-summary", Static).update(f"[b]{len(items)}[/b] items · {short_size(sum(i.size for i in items))}   "
                                                      f"[dim](bin folder: {b.root})[/dim]")

    def _selected(self):
        t = self.query_one("#bin-table", DataTable)
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value if t.row_count else None

    def action_close(self) -> None:
        self.app.pop_screen()
        self.app.state_changed()

    def action_restore(self) -> None:
        k = self._selected()
        if k:
            try:
                dst = self.app.bin().restore(k)
                self.app.notify(f"Restored → {dst}")
                self.app.log_line(f"restored {dst}")
            except Exception as ex:
                self.app.notify(str(ex), severity="error")
            self.reload()

    def action_purge(self) -> None:
        k = self._selected()
        if k:
            self._confirm("Delete this item [b]permanently[/b]?", lambda: self.app.bin().purge(k))

    def _confirm(self, text: str, fn) -> None:
        def go(ok):
            if ok:
                fn()
                self.app.log_line("bin: permanent delete")
                self.reload()
        if self.app.cfg.trash.confirm_permanent:
            self.app.push_screen(ConfirmModal(text, "Delete permanently", True), go)
        else:
            go(True)

    def on_button_pressed(self, e: Button.Pressed) -> None:
        bid = e.button.id
        if bid == "back":
            self.action_close()
        elif bid == "restore":
            self.action_restore()
        elif bid == "purge":
            self.action_purge()
        elif bid == "empty":
            self._confirm("Empty the bin? Everything in it is deleted [b]permanently[/b].", lambda: self.app.bin().empty())
