import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio  # noqa: E402

from . import APP_ID  # noqa: E402
from .window import KilnWindow  # noqa: E402


class KilnApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)

    def do_activate(self):
        window = self.get_active_window() or KilnWindow(self)
        window.present()

    def do_open(self, files, _n_files, _hint):
        self.activate()
        paths = [f.get_path() for f in files if f.get_path()]
        if paths:
            self.get_active_window().open_paths(paths)


def main():
    return KilnApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
