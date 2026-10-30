"""iPhone UI in the Primer design of the desktop app: tabs "Scan" and "Documents", page view with sharing.

Like the desktop app: persistent session (scan button on the panel), every scan is saved as PDF right away.
PDFs live in ``Documents`` (visible in "Files"), index, thumbnails and identity in ``Library``.
Every screen is rebuilt from the state (:meth:`render`), also when switching between light and dark.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import traceback
from pathlib import Path

import toga
from toga.style import Pack

from scansnap.adapters.ix1500.control import CONTROL_PORT
from scansnap.adapters.ix1500.device import Device
from scansnap.adapters.ix1500.identity import load_or_create
from scansnap.adapters.library.library import Library, read_pdf_pages
from scansnap.core.document import FEED_HELP, PRESETS, Entry, ScanPage, default_name, thumbnail
from scansnap.core.errors import FeedError, NoPaper
from scansnap.core.images import is_blank
from scansnap.core.model import ScanSettings as Settings

from . import theme
from .theme import Theme

RECONNECT_EVERY = 20.0  # like the desktop app: keep the session, otherwise the scan button press does not arrive
IDENTITY_NAME = "scansnap-iphone"  # user name on the touch panel (max. 30 characters)
GUTTER = 16


def scanner_reachable(ip: str, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, CONTROL_PORT), timeout=timeout):
            return True
    except OSError:
        return False


class ScanSnackApp(toga.App):
    def startup(self) -> None:
        # on iOS paths.data is "Documents" and thus visible in "Files" – only the PDFs go there
        data = Path.home() / "Library" / "Application Support" / "ScanSnack"
        data.mkdir(parents=True, exist_ok=True)
        self.docs = Path.home() / "Documents"
        self.settings_file = data / "settings.json"
        self.prefs = self._load_prefs()
        self.ident = load_or_create(data / "identity.json", name=IDENTITY_NAME)
        self.library = Library(data / "library.json")
        self._relocate_entries()
        self.device: Device | None = None
        self.scanning = False
        self.dev_lock = threading.Lock()
        self.status_text = ""
        self.connection_state = "not connected"

        # screen: "main" (tabs), "scanner" (enter IP) or "document" (page view)
        self.screen = "main" if self.prefs.get("ip") else "scanner"
        self.tab = 0
        self.open_entry: Entry | None = None
        self.scanner_message = ""

        self.main_window = toga.MainWindow(title=self.formal_name)
        self.theme = Theme(theme.mode())
        self.render()
        self.main_window.show()
        if self.prefs.get("ip"):
            self._connect_in_background()
        self.loop.create_task(self._keep_connected())
        self.loop.create_task(self._follow_appearance())

    # --- settings -----------------------------------------------------------

    def _load_prefs(self) -> dict:
        try:
            return json.loads(self.settings_file.read_text())
        except (OSError, ValueError):
            return {}

    def _store_prefs(self) -> None:
        self.settings_file.write_text(json.dumps(self.prefs))

    def _relocate_entries(self) -> None:
        """The app container changes its path on reinstall; find entries again by file name."""
        for e in self.library.entries:
            moved = self.docs / Path(e.path).name
            if not e.exists and moved.exists():
                e.path = str(moved)
        self.library.entries = [e for e in self.library.entries if e.exists]

    @property
    def preset_index(self) -> int:
        return min(self.prefs.get("preset", 0), len(PRESETS) - 1)

    def _options(self) -> list[bool]:
        p = PRESETS[self.preset_index]
        return self.prefs.get("options") or [p.duplex, p.skip_blank, p.whiten]

    # --- building blocks (Primer) -------------------------------------------

    def label(self, text: str, *, muted: bool = False, size: int = 15, bold: bool = False, **style) -> toga.Label:
        """Text in Primer colors. Toga makes labels as wide as their text; where it can get long, set ``width``,
        otherwise the page overflows sideways (iOS then truncates with "…")."""
        t = self.theme
        label = toga.Label(
            text,
            style=Pack(
                color=t["fgColor-muted" if muted else "fgColor-default"],
                font_size=size,
                font_weight="bold" if bold else "normal",
                **style,
            ),
        )
        if "width" in style:
            label._impl.native.lineBreakMode = 4  # NSLineBreakByTruncatingTail
        return label

    def button(self, text: str, on_press, kind: str = "default", height: int = 40, **style) -> toga.Button:
        """Primer button: ``default`` (gray), ``primary`` (green), ``invisible`` (text only), ``danger``."""
        t = self.theme
        colors = {
            "default": ("control-bgColor-rest", "control-fgColor-rest", "borderColor-default"),
            "primary": ("button-primary-bgColor-rest", "fgColor-onEmphasis", "borderColor-translucent"),
            "danger": ("control-bgColor-rest", "fgColor-danger", "borderColor-default"),
        }
        if kind == "invisible":
            b = toga.Button(text, on_press=on_press, style=Pack(color=t["fgColor-accent"], height=height, **style))
            return b
        bg, fg, border = colors[kind]
        b = toga.Button(
            text,
            on_press=on_press,
            style=Pack(
                background_color=t[bg],
                color=t[fg],
                height=height,
                font_weight="bold" if kind == "primary" else "normal",
                **style,
            ),
        )
        theme.rounded(b, t, border)
        return b

    def card(self, children: list, **style) -> toga.Box:
        """White area with a border, like the lists of the desktop app."""
        box = toga.Box(
            children=children, style=Pack(direction="column", background_color=self.theme["bgColor-default"], **style)
        )
        theme.rounded(box, self.theme)
        return box

    def separator(self) -> toga.Box:
        return toga.Box(style=Pack(height=1, background_color=self.theme["borderColor-default"]))

    @property
    def content_width(self) -> int:
        return max(200, int(self.main_window.size.width) - 2 * GUTTER)

    def page(self, children: list, *, scroll: bool = True) -> tuple[toga.Widget, toga.Box]:
        """Screen area: Primer background (muted), side margin, scrollable if needed.

        Returns (container, content). In Pack the margin is an outer spacing, hence always a wrapper around it.
        """
        bg = self.theme["bgColor-muted"]
        # fixed width: in a tab that is not visible right now Toga otherwise uses the content width
        body = toga.Box(
            children=children,
            style=Pack(direction="column", margin=GUTTER, gap=12, width=self.content_width, background_color=bg),
        )
        if scroll:
            return toga.ScrollContainer(content=body, horizontal=False, style=Pack(flex=1, background_color=bg)), body
        return toga.Box(children=[body], style=Pack(direction="column", flex=1, background_color=bg)), body

    # --- layout -------------------------------------------------------------

    def render(self) -> None:
        theme.tint_window(self.main_window, self.theme)
        self.main_window.title = (
            self.open_entry.name if self.screen == "document" and self.open_entry else self.formal_name
        )
        if self.screen == "scanner":
            self.main_window.content = self._scanner_screen()
        elif self.screen == "document" and self.open_entry is not None:
            self.main_window.content = self._document_screen(self.open_entry)
        else:
            self.screen = "main"
            self.main_window.content = self._main_screen()

    async def _follow_appearance(self) -> None:
        """Light/dark follows the system (Toga does not report the switch, hence polling)."""
        while True:
            await asyncio.sleep(1.0)
            scheme = theme.mode()
            if scheme != self.theme.scheme:
                self.theme = Theme(scheme)
                self.render()

    def _main_screen(self) -> toga.Widget:
        self.tabs = toga.OptionContainer(
            content=[("Scan", self._scan_tab()), ("Documents", self._documents_tab())], on_select=self._tab_selected
        )
        theme.tab_symbols(self.tabs, ["doc.viewfinder", "folder"])
        self.tabs.current_tab = self.tab
        return self.tabs

    def _tab_selected(self, widget, **kwargs) -> None:
        self.tab = widget.content.index(widget.current_tab)

    # --- tab "Scan" ---------------------------------------------------------

    def _scan_tab(self) -> toga.Widget:
        t = self.theme
        self.preset_rows: list[toga.Box] = []
        rows = []
        for i, p in enumerate(PRESETS):
            w = self.content_width - 2 * 4 - 2 * 12 - 2  # card margin, row spacing, border
            row = toga.Box(
                children=[self.label(p.name, width=w), self.label(p.summary, muted=True, size=13, width=w)],
                style=Pack(direction="column", margin=(8, 12), gap=2),
            )
            holder = toga.Box(children=[row], style=Pack(direction="column"))
            theme.on_tap(holder, lambda i=i: self._select_preset(i))
            self.preset_rows.append(holder)
            rows.append(holder)
        presets = self.card([toga.Box(children=rows, style=Pack(direction="column", margin=4, gap=2))])
        self._mark_preset()

        duplex, skip_blank, whiten = self._options()
        self.duplex = toga.Switch("Double-sided", value=duplex)
        self.skip_blank = toga.Switch("Remove blank pages", value=skip_blank)
        self.whiten = toga.Switch("Remove background", value=whiten)
        option_rows = []
        for i, switch in enumerate((self.duplex, self.skip_blank, self.whiten)):
            switch.style.update(color=t["fgColor-default"], font_size=15, margin=(10, 12))
            theme.switch_tint(switch, t)
            switch.on_change = self._option_changed
            if i:
                option_rows.append(self.separator())
            option_rows.append(switch)
        options = self.card(option_rows)

        self.scan_button = self.button("Scan", self.start_scan, "primary", height=56, font_size=20)
        self._mark_scanning()
        self.scanner_label = self.label("", muted=True, size=13, width=self.content_width - 84 - 8)
        self._update_scanner_label(self.connection_state)
        scanner_row = toga.Box(
            children=[self.scanner_label, self.button("Change", self._show_scanner, height=32, font_size=13, width=84)],
            style=Pack(direction="row", align_items="center", gap=8),
        )
        self.status_label = self.label(self.status_text, muted=True, size=13, width=self.content_width)
        return self.page(
            [
                self.label("Profile", bold=True),
                presets,
                self.label("Options", bold=True),
                options,
                self.scan_button,
                scanner_row,
                self.status_label,
            ]
        )[0]

    def _mark_preset(self) -> None:
        """Selection as in the desktop list: pale accent background with an accent border."""
        t = self.theme
        for i, holder in enumerate(self.preset_rows):
            selected = i == self.preset_index
            holder.style.background_color = t["bgColor-accent-muted"] if selected else t["bgColor-default"]
            theme.rounded(holder, t, "bgColor-accent-emphasis" if selected else None)

    def _select_preset(self, idx: int) -> None:
        if idx == self.preset_index:
            return
        self.prefs["preset"] = idx
        self.prefs.pop("options", None)
        self._store_prefs()
        self._mark_preset()
        p = PRESETS[idx]
        for switch, value in ((self.duplex, p.duplex), (self.skip_blank, p.skip_blank), (self.whiten, p.whiten)):
            switch.on_change = None
            switch.value = value
            switch.on_change = self._option_changed

    def _option_changed(self, widget, **kwargs) -> None:
        self.prefs["options"] = [self.duplex.value, self.skip_blank.value, self.whiten.value]
        self._store_prefs()

    def _mark_scanning(self) -> None:
        t = self.theme
        self.scan_button.text = "Scanning …" if self.scanning else "Scan"
        self.scan_button.enabled = not self.scanning
        self.scan_button.style.background_color = t[
            "button-primary-bgColor-disabled" if self.scanning else "button-primary-bgColor-rest"
        ]

    def _set_status(self, text: str) -> None:
        self.status_text = text
        if self.screen == "main":
            self.status_label.text = text

    def _update_scanner_label(self, state: str | None = None) -> None:
        if state is None:
            state = "connected" if self.device and self.device.connected else "not connected"
        self.connection_state = state
        if self.screen == "main":
            ip = self.prefs.get("ip", "")
            self.scanner_label.text = f"Scanner {ip}\n{state}" if ip else "No scanner"

    # --- tab "Documents" ----------------------------------------------------

    def _documents_tab(self) -> toga.Widget:
        container, self.documents_body = self.page([])
        self._fill_documents()
        return container

    def _fill_documents(self) -> None:
        body = self.documents_body
        body.clear()
        if not self.library.entries:
            body.add(
                self.label(
                    "No documents yet.\nLoad paper into the scanner and press “Scan”.",
                    muted=True,
                    width=self.content_width,
                    text_align="center",
                    margin_top=48,
                )
            )
            return
        rows = []
        for i, e in enumerate(self.library.entries):
            if i:
                rows.append(self.separator())
            thumb = self.library.thumb_path(e)
            icon = (
                toga.ImageView(toga.Image(thumb), style=Pack(width=40, height=52))
                if thumb.exists()
                else toga.Box(style=Pack(width=40, height=52))
            )
            w = self.content_width - 2 * 12 - 40 - 2 * 12 - 12  # margins, thumbnail, spacing, arrow
            text = toga.Box(
                children=[
                    self.label(e.name, width=w),
                    self.label(f"{e.pages} page(s) · {e.created.replace('T', ' ')[:16]}", width=w, muted=True, size=13),
                ],
                style=Pack(direction="column", gap=2, flex=1),
            )
            row = toga.Box(
                children=[icon, text, self.label("›", muted=True, size=20)],
                style=Pack(direction="row", align_items="center", gap=12, margin=(8, 12)),
            )
            theme.on_tap(row, lambda e=e: self._open_document(e))
            rows.append(row)
        body.add(self.card(rows))

    # --- page view ----------------------------------------------------------

    def _open_document(self, entry: Entry) -> None:
        self.screen, self.open_entry = "document", entry
        self.render()

    def _close_document(self) -> None:
        self.screen, self.open_entry, self.tab = "main", None, 1
        self.render()

    def _document_screen(self, entry: Entry) -> toga.Widget:
        t = self.theme
        width = self.content_width
        pages_box = toga.Box(style=Pack(direction="column", gap=12, margin=GUTTER, background_color=t["bgColor-muted"]))
        loading = self.label("Loading …", muted=True, width=self.content_width)
        pages_box.add(loading)

        info = self.label(f"{entry.pages} page(s)", muted=True, size=13)
        top = toga.Box(
            children=[
                self.button("‹ Documents", lambda w, **k: self._close_document(), "invisible", height=40, width=110),
                toga.Box(style=Pack(flex=1)),
                info,
            ],
            style=Pack(
                direction="row", align_items="center", margin=(0, GUTTER, 0, 4), background_color=t["bgColor-default"]
            ),
        )
        bottom = toga.Box(
            children=[
                self.button("Delete", lambda w, **k: self._confirm_delete(entry), "danger", width=96),
                toga.Box(style=Pack(flex=1)),
                self.button("Share", lambda w, **k: self.share_file(entry.file), "primary", width=120),
            ],
            style=Pack(
                direction="row", align_items="center", margin=(10, GUTTER), background_color=t["bgColor-default"]
            ),
        )
        screen = toga.Box(
            children=[
                top,
                self.separator(),
                toga.ScrollContainer(
                    content=pages_box, horizontal=False, style=Pack(flex=1, background_color=t["bgColor-muted"])
                ),
                self.separator(),
                toga.Box(children=[bottom], style=Pack(direction="column", background_color=t["bgColor-default"])),
            ],
            style=Pack(direction="column", background_color=t["bgColor-default"]),
        )

        def run() -> None:
            try:
                thumbs = [thumbnail(p.rendered(), 1000) for p in read_pdf_pages(entry.file)]
            except Exception as e:  # noqa: BLE001
                self.loop.call_soon_threadsafe(setattr, loading, "text", f"Cannot read {entry.file.name}:\n{e}")
                return
            self.loop.call_soon_threadsafe(show, thumbs)

        def show(thumbs: list[bytes]) -> None:
            if self.open_entry is not entry:
                return
            pages_box.remove(loading)
            for png in thumbs:
                pages_box.add(toga.ImageView(toga.Image(png), style=Pack(width=width)))

        threading.Thread(target=run, daemon=True).start()
        return screen

    def _confirm_delete(self, entry: Entry) -> None:
        async def ask() -> None:
            dialog = toga.ConfirmDialog("Delete document?", f"“{entry.name}” will be deleted from this iPhone.")
            if await self.main_window.dialog(dialog):
                self.library.remove(entry)
                entry.file.unlink(missing_ok=True)
                self._close_document()

        self.loop.create_task(ask())

    def share_file(self, path: Path) -> None:
        """iOS share sheet (Mail, Save to Files, AirDrop, Print …)."""
        from rubicon.objc import ObjCClass

        url = ObjCClass("NSURL").fileURLWithPath(str(path))
        vc = ObjCClass("UIActivityViewController").alloc().initWithActivityItems([url], applicationActivities=None)
        root = self.main_window._impl.native.rootViewController
        popover = vc.popoverPresentationController  # iPad
        if popover is not None:
            popover.sourceView = root.view
        root.presentViewController(vc, animated=True, completion=None)

    # --- scanner screen -----------------------------------------------------

    def _scanner_screen(self) -> toga.Widget:
        t = self.theme
        self.ip_input = toga.TextInput(
            value=self.prefs.get("ip", ""), placeholder="e.g. 192.168.178.29", style=Pack(height=40, font_size=15)
        )
        theme.text_field(self.ip_input, t)
        self.connect_button = self.button("Connect", self._check_ip, "primary", height=44)
        self.scanner_status = self.label(self.scanner_message, muted=True, size=13, width=self.content_width)
        children = [
            self.label("Connect Scanner", bold=True, size=22),
            self.label(
                "Enter the IP address of your ScanSnap.\nIt must be in the same Wi-Fi network.",
                muted=True,
                width=self.content_width,
            ),
            self.label("Scanner IP address", bold=True, size=13, margin_top=8),
            self.ip_input,
            self.connect_button,
            self.scanner_status,
        ]
        if self.prefs.get("ip"):
            children.append(self.button("Cancel", lambda w, **k: self._show_main()))
        return self.page(children)[0]

    def _show_scanner(self, widget=None, message: str = "", **kwargs) -> None:
        self.screen, self.scanner_message = "scanner", message
        self.render()

    def _show_main(self) -> None:
        self.screen = "main"
        self.render()

    def _check_ip(self, widget, **kwargs) -> None:
        ip = self.ip_input.value.strip()
        if not ip:
            return
        self.connect_button.enabled = False
        self.scanner_status.text = f"Checking {ip} …"

        def run() -> None:
            ok = scanner_reachable(ip)
            self.loop.call_soon_threadsafe(self._ip_checked, ip, ok)

        threading.Thread(target=run, daemon=True).start()

    def _ip_checked(self, ip: str, ok: bool) -> None:
        if self.screen != "scanner":
            return
        self.connect_button.enabled = True
        if not ok:
            self.scanner_message = (
                f"No scanner at {ip}.\nIs it switched on and in the same Wi-Fi?\n"
                "Allow local network access if iOS asks."
            )
            self.scanner_status.text = self.scanner_message
            return
        if ip != self.prefs.get("ip"):
            self.prefs["ip"] = ip
            self._store_prefs()
            self._drop_device()
        self._show_main()
        self._connect_in_background()

    # --- connection ---------------------------------------------------------

    def _device(self) -> Device:
        ip = self.prefs["ip"]
        if self.device is None or self.device.ip != ip:
            self._drop_device()
            self.device = Device(
                ip,
                self.ident,
                keep_alive=True,
                on_button=lambda _note: self.loop.call_soon_threadsafe(self._panel_scan),
            )
        return self.device

    def _drop_device(self) -> None:
        if self.device is not None:
            dev, self.device = self.device, None
            threading.Thread(target=dev.disconnect, daemon=True).start()

    def _connect_in_background(self) -> None:
        if not self.prefs.get("ip") or self.scanning:
            return
        device = self._device()
        if device.connected:
            return
        self._update_scanner_label("connecting …")

        def run() -> None:
            with self.dev_lock:
                try:
                    if not device.connected:
                        device.connect()
                except Exception as e:  # noqa: BLE001 – the scan tries again
                    print(f"Connection failed: {e}", file=sys.stderr)
            self.loop.call_soon_threadsafe(self._update_scanner_label)

        threading.Thread(target=run, daemon=True).start()

    async def _keep_connected(self) -> None:
        while True:
            await asyncio.sleep(RECONNECT_EVERY)
            if not self.scanning:
                self._connect_in_background()

    # --- scanning -----------------------------------------------------------

    def _panel_scan(self) -> None:
        if not self.scanning:
            self.start_scan()

    def start_scan(self, widget=None, **kwargs) -> None:
        if self.scanning:
            return
        if not self.prefs.get("ip"):
            self._show_scanner()
            return
        preset = PRESETS[self.preset_index]
        duplex, skip_blank, whiten = self._options()
        settings = Settings(preset.mode, preset.dpi, duplex)
        device = self._device()
        self.scanning = True
        if self.screen != "main" or self.tab != 0:
            self.screen, self.tab = "main", 0
            self.render()
        self._mark_scanning()
        self._set_status("Scanning …" if device.connected else "Connecting to the scanner …")
        pages: list[ScanPage] = []

        def post(fn, *args) -> None:
            self.loop.call_soon_threadsafe(fn, *args)

        def run() -> None:
            skipped = 0
            error, connection = "", False
            with self.dev_lock:
                try:
                    for sheet in device.scan(settings, whiten=whiten):
                        for jpeg in (sheet.front, sheet.back):
                            if jpeg is None:
                                continue
                            if skip_blank and is_blank(jpeg):
                                skipped += 1
                                continue
                            pages.append(ScanPage(jpeg, preset.dpi))
                            post(self._set_status, f"{len(pages)} page(s) scanned …")
                except NoPaper:
                    error = "No paper in the feeder."
                except FeedError as e:
                    error = FEED_HELP.get(e.reason, f"The scanner reported a paper problem ({e.reason}).")
                    if pages:
                        error += f"\n\nThe {len(pages)} page(s) scanned before are kept."
                except OSError as e:
                    traceback.print_exc(file=sys.stderr)
                    error, connection = f"Cannot reach the scanner at {device.ip}:\n{e}", True
                except Exception as e:  # noqa: BLE001 – message to the UI
                    traceback.print_exc(file=sys.stderr)
                    error = f"Scan failed: {e}"
            entry, save_error = None, ""
            if pages:
                try:
                    entry = self.library.add(pages, self.docs, default_name())
                except Exception as e:  # noqa: BLE001
                    traceback.print_exc(file=sys.stderr)
                    save_error = f"Could not save the PDF: {e}"
            note = f", {skipped} blank page(s) removed" if skipped else ""
            post(self._scan_done, entry, len(pages), note, error or save_error, connection)

        threading.Thread(target=run, daemon=True).start()

    def _scan_done(self, entry: Entry | None, count: int, note: str, error: str, connection: bool) -> None:
        self.scanning = False
        self.status_text = f"Saved “{entry.name}”\n{count} page(s){note}." if entry else ""
        if self.screen == "main":
            self._mark_scanning()
            self._update_scanner_label()
            self._set_status(self.status_text)
            self._fill_documents()
        if connection:
            self._show_scanner(message=error)
        elif error:
            self.loop.create_task(self.main_window.dialog(toga.InfoDialog("Scan", error)))
        if entry and not error:
            self._open_document(entry)  # like the desktop app: show the pages right away


def main() -> ScanSnackApp:
    return ScanSnackApp()  # name and bundle ID from the Briefcase metadata
