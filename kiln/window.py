import os
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from . import audio, jobs, media  # noqa: E402
from .process import Cancelled, CommandFailed, Runner  # noqa: E402


def run_in_thread(work, done):
    """Run work() in a thread and deliver done(result, error) on the main loop."""
    def target():
        try:
            result, error = work(), None
        except Exception as e:      # reported to the user by done()
            result, error = None, e
        GLib.idle_add(done, result, error)
    threading.Thread(target=target, daemon=True).start()


def human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def icon_button(icon, tooltip, callback, *args):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    button.add_css_class("flat")
    button.connect("clicked", lambda _b: callback(*args))
    return button


def files_from_drop(value):
    if isinstance(value, Gdk.FileList):
        return [f.get_path() for f in value.get_files() if f.get_path()]
    return []


# ---------------------------------------------------------------- burn dialog


class BurnDialog(Adw.Dialog):
    def __init__(self, title, on_cancel):
        super().__init__(title=title, content_width=440, can_close=False)
        self._on_cancel = on_cancel
        self._finished = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        self.phase = Gtk.Label(label="Starting", xalign=0)
        self.phase.add_css_class("title-3")
        self.bar = Gtk.ProgressBar(show_text=True)
        self.detail = Gtk.Label(xalign=0, wrap=True)
        self.detail.add_css_class("dim-label")

        self.log = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.CHAR)
        scroller = Gtk.ScrolledWindow(child=self.log, min_content_height=160)
        self.log_expander = Gtk.Expander(label="Details", child=scroller, visible=False)

        self.button = Gtk.Button(label="Cancel", halign=Gtk.Align.END)
        self.button.connect("clicked", self._clicked)

        for w in (self.phase, self.bar, self.detail, self.log_expander, self.button):
            box.append(w)

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(header)
        self.set_child(view)
        self._pulse_id = 0

    def update(self, phase, fraction, detail):
        if self._finished:
            return
        self.phase.set_label(phase)
        if fraction is None:
            if not self._pulse_id:
                self._pulse_id = GLib.timeout_add(120, self._pulse)
            self.bar.set_text("")
        else:
            self._stop_pulse()
            self.bar.set_fraction(fraction)
            self.bar.set_text(f"{fraction * 100:.0f}%")
        self.detail.set_label(detail or "")

    def _pulse(self):
        self.bar.pulse()
        return True

    def _stop_pulse(self):
        if self._pulse_id:
            GLib.source_remove(self._pulse_id)
            self._pulse_id = 0

    def finish(self, ok, message, log_text=""):
        self._finished = True
        self._stop_pulse()
        self.phase.set_label("Done" if ok else "Failed")
        if ok:
            self.bar.set_fraction(1.0)
            self.bar.set_text("100%")
        self.detail.set_label(message)
        if log_text:
            self.log.get_buffer().set_text(log_text)
            self.log_expander.set_visible(True)
        self.button.set_label("Close")
        self.button.add_css_class("suggested-action")
        self.set_can_close(True)

    def _clicked(self, _button):
        if self._finished:
            self.close()
        else:
            self.button.set_sensitive(False)
            self.button.set_label("Cancelling…")
            self._on_cancel()


# ---------------------------------------------------------------- pages


class Page(Gtk.Box):
    """Common layout: content on top, capacity + burn button at the bottom."""

    title = ""
    icon = ""

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.append(self.stack)

        bar = Gtk.ActionBar()
        self.capacity = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True,
                                     valign=Gtk.Align.CENTER)
        self.capacity.add_offset_value("full", 1.0)
        self.capacity.set_size_request(160, -1)
        self.capacity_label = Gtk.Label()
        self.capacity_label.add_css_class("dim-label")
        self.burn_button = Gtk.Button(label="Burn")
        self.burn_button.add_css_class("suggested-action")
        self.burn_button.connect("clicked", lambda _b: self.burn())

        box = Gtk.Box(spacing=12, hexpand=True)
        box.append(self.capacity)
        box.append(self.capacity_label)
        bar.pack_start(box)
        bar.pack_end(self.burn_button)
        self.append(bar)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", lambda _t, value, _x, _y: self.add_paths(files_from_drop(value)) or True)
        self.add_controller(drop)

    def set_capacity(self, used, total, text):
        self.capacity.set_value(min(used / total, 1.0) if total else 0)
        self.capacity_label.set_label(text)

    def refresh(self):
        pass

    def add_paths(self, paths):
        pass

    def burn(self):
        pass


class TrackRow(Gtk.ListBoxRow):
    def __init__(self, page, track):
        super().__init__(activatable=False)
        self.track = track
        box = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=12, margin_end=6)
        self.number = Gtk.Label(width_chars=3, xalign=1)
        self.number.add_css_class("numeric")
        self.number.add_css_class("dim-label")

        self.title = Gtk.Entry(text=track.title, hexpand=True, placeholder_text="Title")
        self.artist = Gtk.Entry(text=track.artist, hexpand=True, placeholder_text="Artist")
        self.title.connect("changed", lambda e: setattr(track, "title", e.get_text()))
        self.artist.connect("changed", lambda e: setattr(track, "artist", e.get_text()))

        length = Gtk.Label(label=audio.format_duration(track.duration), width_chars=6, xalign=1)
        length.add_css_class("numeric")

        box.append(self.number)
        box.append(self.title)
        box.append(self.artist)
        box.append(length)
        box.append(icon_button("go-up-symbolic", "Move up", page.move, self, -1))
        box.append(icon_button("go-down-symbolic", "Move down", page.move, self, 1))
        box.append(icon_button("user-trash-symbolic", "Remove", page.remove, self))
        self.set_child(box)


class AudioPage(Page):
    title = "Audio CD"
    icon = "audio-x-generic-symbolic"

    def __init__(self, window):
        super().__init__(window)
        self.rows = []

        empty = Adw.StatusPage(icon_name="audio-x-generic-symbolic", title="Audio CD",
                               description="Drop music files here, or add them below.")
        add = Gtk.Button(label="Add Music…", halign=Gtk.Align.CENTER)
        add.add_css_class("pill")
        add.add_css_class("suggested-action")
        add.connect("clicked", lambda _b: self.choose())
        empty.set_child(add)
        self.stack.add_named(empty, "empty")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        album = Adw.PreferencesGroup(title="Disc")
        self.album_title = Adw.EntryRow(title="Album title")
        self.album_artist = Adw.EntryRow(title="Album artist")
        album.add(self.album_title)
        album.add(self.album_artist)

        tracks = Adw.PreferencesGroup(title="Tracks")
        add_more = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add music")
        add_more.add_css_class("flat")
        add_more.connect("clicked", lambda _b: self.choose())
        tracks.set_header_suffix(add_more)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        tracks.add(self.list)

        content.append(album)
        content.append(tracks)
        clamp = Adw.Clamp(child=content, maximum_size=900)
        self.stack.add_named(Gtk.ScrolledWindow(child=clamp), "tracks")
        self.update()

    @property
    def tracks(self):
        return [r.track for r in self.rows]

    def choose(self):
        dialog = Gtk.FileDialog(title="Add Music")
        f = Gtk.FileFilter(name="Audio")
        f.add_mime_type("audio/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)

        def done(d, result):
            try:
                files = d.open_multiple_finish(result)
            except GLib.Error:
                return
            self.add_paths([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dialog.open_multiple(self.window, None, done)

    def add_paths(self, paths):
        files = []
        for p in paths:
            if os.path.isdir(p):
                files += sorted(os.path.join(p, n) for n in os.listdir(p)
                                if os.path.isfile(os.path.join(p, n)))
            else:
                files.append(p)

        def work():
            found, skipped = [], []
            for path in files:
                try:
                    found.append(audio.probe(path))
                except Exception:
                    skipped.append(os.path.basename(path))
            return found, skipped

        def done(result, error):
            if error:
                self.window.toast(str(error))
                return
            found, skipped = result
            for track in found:
                row = TrackRow(self, track)
                self.rows.append(row)
                self.list.append(row)
            if skipped:
                self.window.toast(f"Skipped {len(skipped)} file(s) without audio")
            if found and not self.album_title.get_text():
                albums = {t.artist for t in found if t.artist}
                if len(albums) == 1:
                    self.album_artist.set_text(albums.pop())
            self.update()
        run_in_thread(work, done)

    def move(self, row, delta):
        i = self.rows.index(row)
        j = i + delta
        if 0 <= j < len(self.rows):
            self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
            self.list.remove(row)
            self.list.insert(row, j)
            self.update()

    def remove(self, row):
        self.rows.remove(row)
        self.list.remove(row)
        self.update()

    def update(self):
        for i, row in enumerate(self.rows, 1):
            row.number.set_label(f"{i}")
        self.stack.set_visible_child_name("tracks" if self.rows else "empty")
        seconds = sum(t.duration for t in self.tracks)
        m = self.window.media
        total = audio.CD_MAX_SECONDS
        if m and m.present and m.is_cd and m.blank and m.free_sectors:
            total = m.free_sectors / media.CD_SECTORS_PER_SECOND
        self.set_capacity(seconds, total,
                          f"{audio.format_duration(seconds)} of {audio.format_duration(total)}")
        self.burn_button.set_sensitive(bool(self.rows))

    def refresh(self):
        self.update()

    def burn(self):
        tracks = self.tracks
        m = self.window.media
        try:
            jobs.check_audio(m, tracks)
        except jobs.PreflightError as e:
            self.window.error("Can't burn yet", str(e))
            return
        title = self.album_title.get_text()
        artist = self.album_artist.get_text()
        self.window.start_job("Burning Audio CD", lambda d, s, e, r, rep:
                              jobs.run_audio(d, tracks, title, artist, s, e, r, rep))


class ItemRow(Adw.ActionRow):
    def __init__(self, page, path, size):
        super().__init__(title=GLib.markup_escape_text(os.path.basename(path) or path),
                         subtitle=GLib.markup_escape_text(os.path.dirname(path)))
        self.path = path
        self.size = size
        icon = "folder-symbolic" if os.path.isdir(path) else "text-x-generic-symbolic"
        self.add_prefix(Gtk.Image(icon_name=icon))
        size_label = Gtk.Label(label=human_size(size))
        size_label.add_css_class("dim-label")
        self.add_suffix(size_label)
        self.add_suffix(icon_button("user-trash-symbolic", "Remove", page.remove, self))


class DataPage(Page):
    title = "Data Disc"
    icon = "folder-symbolic"

    def __init__(self, window):
        super().__init__(window)
        self.rows = []

        empty = Adw.StatusPage(icon_name="folder-symbolic", title="Data Disc",
                               description="Drop files and folders here, or add them below.")
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        for label, folders in (("Add Files…", False), ("Add Folder…", True)):
            b = Gtk.Button(label=label)
            b.add_css_class("pill")
            b.connect("clicked", lambda _b, f=folders: self.choose(f))
            buttons.append(b)
        empty.set_child(buttons)
        self.stack.add_named(empty, "empty")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        disc = Adw.PreferencesGroup(title="Disc")
        self.label = Adw.EntryRow(title="Disc name")
        self.label.set_text("Data disc")
        disc.add(self.label)

        files = Adw.PreferencesGroup(title="Contents")
        suffix = Gtk.Box(spacing=6)
        suffix.append(icon_button("document-new-symbolic", "Add files", self.choose, False))
        suffix.append(icon_button("folder-new-symbolic", "Add folder", self.choose, True))
        files.set_header_suffix(suffix)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        files.add(self.list)

        content.append(disc)
        content.append(files)
        self.stack.add_named(Gtk.ScrolledWindow(child=Adw.Clamp(child=content, maximum_size=900)),
                             "items")
        self.update()

    def choose(self, folders):
        dialog = Gtk.FileDialog(title="Add Folder" if folders else "Add Files")

        def done(d, result):
            try:
                if folders:
                    items = d.select_multiple_folders_finish(result)
                else:
                    items = d.open_multiple_finish(result)
            except GLib.Error:
                return
            self.add_paths([items.get_item(i).get_path() for i in range(items.get_n_items())])

        if folders:
            dialog.select_multiple_folders(self.window, None, done)
        else:
            dialog.open_multiple(self.window, None, done)

    def add_paths(self, paths):
        existing = {r.path for r in self.rows}
        paths = [p for p in paths if p not in existing]

        def done(sizes, error):
            if error:
                self.window.toast(str(error))
                return
            for path, size in zip(paths, sizes):
                row = ItemRow(self, path, size)
                self.rows.append(row)
                self.list.append(row)
            self.update()
        run_in_thread(lambda: [jobs.tree_size(p) for p in paths], done)

    def remove(self, row):
        self.rows.remove(row)
        self.list.remove(row)
        self.update()

    def used_bytes(self):
        # ~2% for ISO 9660 / Joliet / Rock Ridge directory overhead
        return int(sum(r.size for r in self.rows) * 1.02) + 2 * 2**20

    def update(self):
        self.stack.set_visible_child_name("items" if self.rows else "empty")
        used = self.used_bytes() if self.rows else 0
        m = self.window.media
        total = m.free_bytes if m and m.present and m.free_sectors else 0
        text = human_size(used) + (f" of {human_size(total)}" if total else "")
        self.set_capacity(used, total or max(used, 1), text)
        self.burn_button.set_sensitive(bool(self.rows))

    def refresh(self):
        self.update()

    def burn(self):
        items = [r.path for r in self.rows]
        try:
            jobs.check_writable(self.window.media, self.used_bytes())
        except jobs.PreflightError as e:
            self.window.error("Can't burn yet", str(e))
            return
        label = self.label.get_text()
        self.window.start_job("Burning Data Disc", lambda d, s, e, r, rep:
                              jobs.run_data(d, items, label, s, e, r, rep))


class ImagePage(Page):
    title = "Disc Image"
    icon = "media-optical-symbolic"

    def __init__(self, window):
        super().__init__(window)
        self.image = None
        self.size = 0

        self.status = Adw.StatusPage(icon_name="media-optical-symbolic", title="Disc Image",
                                     description="Burn an .iso image to a disc.")
        choose = Gtk.Button(label="Choose Image…", halign=Gtk.Align.CENTER)
        choose.add_css_class("pill")
        choose.add_css_class("suggested-action")
        choose.connect("clicked", lambda _b: self.choose())
        self.status.set_child(choose)
        self.stack.add_named(self.status, "main")
        self.update()

    def choose(self):
        dialog = Gtk.FileDialog(title="Choose Disc Image")
        f = Gtk.FileFilter(name="Disc images")
        for pattern in ("*.iso", "*.ISO", "*.img"):
            f.add_pattern(pattern)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dialog.set_filters(filters)

        def done(d, result):
            try:
                path = d.open_finish(result).get_path()
            except GLib.Error:
                return
            self.add_paths([path])
        dialog.open(self.window, None, done)

    def add_paths(self, paths):
        paths = [p for p in paths if os.path.isfile(p)]
        if not paths:
            return
        self.image = paths[0]
        self.size = os.path.getsize(self.image)
        self.status.set_title(os.path.basename(self.image))
        self.status.set_description(f"{human_size(self.size)}\n{os.path.dirname(self.image)}")
        self.status.get_child().set_label("Choose Another…")
        self.update()

    def update(self):
        m = self.window.media
        total = m.free_bytes if m and m.present and m.free_sectors else 0
        text = human_size(self.size) + (f" of {human_size(total)}" if total else "") \
            if self.image else ""
        self.set_capacity(self.size, total or max(self.size, 1), text)
        self.burn_button.set_sensitive(self.image is not None)

    def refresh(self):
        self.update()

    def burn(self):
        image = self.image
        try:
            jobs.check_writable(self.window.media, self.size)
        except jobs.PreflightError as e:
            self.window.error("Can't burn yet", str(e))
            return
        self.window.start_job("Burning Image", lambda d, s, e, r, rep:
                              jobs.run_image(d, image, s, e, r, rep))


# ---------------------------------------------------------------- window


class KilnWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Kiln", default_width=900, default_height=680)
        self.drives = []
        self.media = None
        self.busy = False
        self._probing = False

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.view_stack = Adw.ViewStack()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.view_stack,
                                                 policy=Adw.ViewSwitcherPolicy.WIDE))
        view.add_top_bar(header)

        # drive bar
        drive_bar = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8,
                            margin_start=12, margin_end=12)
        drive_bar.append(Gtk.Image(icon_name="drive-optical-symbolic"))
        self.drive_list = Gtk.StringList()
        self.drive_dropdown = Gtk.DropDown(model=self.drive_list)
        self.drive_dropdown.connect("notify::selected", lambda *_: self.probe())
        self.media_label = Gtk.Label(label="Looking for drives…", xalign=0, hexpand=True,
                                     ellipsize=3)
        self.media_label.add_css_class("dim-label")

        self.speed_list = Gtk.StringList.new(["Max speed"])
        self.speed_dropdown = Gtk.DropDown(model=self.speed_list, tooltip_text="Write speed")
        self.eject_switch = Gtk.CheckButton(label="Eject when done", active=True)

        erase_menu = Gio.Menu()
        erase_menu.append("Quick Erase", "win.erase-quick")
        erase_menu.append("Full Erase", "win.erase-full")
        self.erase_button = Gtk.MenuButton(label="Erase", menu_model=erase_menu,
                                           tooltip_text="Erase a rewritable disc")
        for name, full in (("erase-quick", False), ("erase-full", True)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, f=full: self.erase(f))
            self.add_action(action)

        drive_bar.append(self.drive_dropdown)
        drive_bar.append(self.media_label)
        drive_bar.append(self.speed_dropdown)
        drive_bar.append(self.eject_switch)
        drive_bar.append(self.erase_button)
        drive_bar.append(icon_button("view-refresh-symbolic", "Check disc again", self.probe))
        drive_bar.append(icon_button("media-eject-symbolic", "Eject", self.eject))
        view.add_top_bar(drive_bar)

        self.pages = [AudioPage(self), DataPage(self), ImagePage(self)]
        for page in self.pages:
            self.view_stack.add_titled_with_icon(page, page.title, page.title, page.icon)
        view.set_content(self.view_stack)
        self.view_stack.set_visible_child(self.pages[0])
        self.erase_button.set_sensitive(False)

        self.toasts.set_child(view)
        self.set_content(self.toasts)

        self.load_drives()
        GLib.timeout_add_seconds(4, self._poll)

    def open_paths(self, paths):
        """Files opened from the file manager or command line."""
        images = [p for p in paths if p.lower().endswith((".iso", ".img"))]
        page = self.pages[2] if images and len(images) == len(paths) else self.pages[0]
        self.view_stack.set_visible_child(page)
        page.add_paths(paths)

    # ---- helpers

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def error(self, heading, body):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "OK")
        dialog.present(self)

    @property
    def drive(self):
        i = self.drive_dropdown.get_selected()
        return self.drives[i].path if 0 <= i < len(self.drives) else None

    @property
    def speed(self):
        i = self.speed_dropdown.get_selected()
        if i <= 0 or not self.media:
            return None
        return self.media.write_speeds[i - 1]

    # ---- drive state

    def load_drives(self):
        def done(drives, error):
            self.drives = drives or []
            self.drive_list.splice(0, self.drive_list.get_n_items(),
                                   [d.name for d in self.drives])
            if not self.drives:
                self.media_label.set_label("No burner found" if not error else str(error))
                self.set_media(None)
            else:
                self.probe()
        run_in_thread(media.list_drives, done)

    def probe(self):
        drive = self.drive
        if not drive or self.busy or self._probing:
            return
        self._probing = True
        self.media_label.set_label("Checking disc…")

        def done(m, error):
            self._probing = False
            if error:
                self.media_label.set_label(f"Couldn't read the drive: {error}")
                self.set_media(None)
            else:
                self.set_media(m)
        run_in_thread(lambda: media.probe(drive), done)

    def _poll(self):
        # Only poll an empty drive; probing a loaded one would spin it up
        if not self.busy and self.drives and (self.media is None or not self.media.present):
            self.probe()
        return True

    def set_media(self, m):
        self.media = m
        if m is not None:
            text = m.describe()
            if m.product:
                text += f" · {m.product.split(',')[-1].strip()}"
            self.media_label.set_label(text)
        speeds = ["Max speed"] + [f"{s:g}x" for s in (m.write_speeds if m else [])]
        self.speed_list.splice(0, self.speed_list.get_n_items(), speeds)
        self.speed_dropdown.set_selected(0)
        can_erase = bool(m and m.present and m.rewritable)
        self.erase_button.set_sensitive(can_erase)
        if can_erase:
            self.erase_button.set_tooltip_text("Erase a rewritable disc")
        elif m and m.present:
            self.erase_button.set_tooltip_text(f"A {m.kind} can't be erased; only rewritable discs can")
        else:
            self.erase_button.set_tooltip_text("Insert a rewritable disc to erase it")
        for page in self.pages:
            page.refresh()

    def eject(self):
        drive = self.drive
        if drive and not self.busy:
            run_in_thread(lambda: jobs.eject(drive), lambda *_: self.set_media(media.Media()))

    def erase(self, full):
        if self.media and self.media.blank:
            heading = "This disc is already blank"
            body = "Erase it anyway?"
        else:
            heading = "Erase this disc?"
            body = "Everything on it will be permanently deleted."
        if full:
            body += " A full erase can take over ten minutes."
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("erase", "Erase")
        dialog.set_response_appearance("erase", Adw.ResponseAppearance.DESTRUCTIVE)

        def response(_d, r):
            if r == "erase":
                self.start_job("Erasing Disc", lambda d, s, e, run, rep:
                               jobs.run_blank(d, full, run, rep))
        dialog.connect("response", response)
        dialog.present(self)

    # ---- jobs

    def start_job(self, title, job):
        drive = self.drive
        if not drive or self.busy:
            return
        speed, eject_after = self.speed, self.eject_switch.get_active()
        runner = Runner()
        dialog = BurnDialog(title, runner.cancel)
        self.busy = True
        app = self.get_application()
        cookie = app.inhibit(self, Gtk.ApplicationInhibitFlags.SUSPEND |
                             Gtk.ApplicationInhibitFlags.LOGOUT, title)

        def report(phase, fraction, detail):
            GLib.idle_add(dialog.update, phase, fraction, detail)

        def done(message, error):
            app.uninhibit(cookie)
            self.busy = False
            if error is None:
                dialog.finish(True, message)
            elif isinstance(error, Cancelled):
                dialog.finish(False, "Cancelled. A partly written disc may need erasing.")
            elif isinstance(error, CommandFailed):
                dialog.finish(False, f"The burn failed ({error}).", error.tail)
            else:
                dialog.finish(False, str(error))
            self.probe()

        dialog.present(self)
        run_in_thread(lambda: job(drive, speed, eject_after, runner, report), done)
