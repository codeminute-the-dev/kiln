"""Run burn tools and stream their output.

cdrskin and xorriso redraw progress with carriage returns, so output is split
on both \\r and \\n before it reaches the line callback.
"""

import os
import shlex
import signal
import subprocess
import threading
import time

_log_lock = threading.Lock()


def log_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    os.makedirs(os.path.join(base, "kiln"), exist_ok=True)
    return os.path.join(base, "kiln", "kiln.log")


def log(text):
    """Append to ~/.cache/kiln/kiln.log so failures can be diagnosed later."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with _log_lock, open(log_path(), "a", encoding="utf-8") as f:
        for line in text.splitlines() or [""]:
            f.write(f"{stamp} {line}\n")


class Cancelled(Exception):
    pass


class CommandFailed(Exception):
    def __init__(self, argv, returncode, tail):
        self.argv = argv
        self.returncode = returncode
        self.tail = tail
        super().__init__(f"{os.path.basename(argv[0])} exited with status {returncode}")


class Runner:
    """Runs one command at a time; cancel() may be called from another thread."""

    def __init__(self):
        self._proc = None
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        proc = self._proc
        if proc and proc.poll() is None:
            # SIGINT lets cdrskin/xorriso release the drive cleanly
            proc.send_signal(signal.SIGINT)

    def run(self, argv, on_line=None, tail_lines=40):
        if self.cancelled:
            raise Cancelled()

        tail = []
        log(f"$ {shlex.join(argv)}")
        self._proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, bufsize=0)
        buf = b""
        try:
            while True:
                chunk = self._proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.replace(b"\r", b"\n").split(b"\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace").rstrip()
                    if not line:
                        continue
                    tail = (tail + [line])[-tail_lines:]
                    if not line.startswith("Track ") or " of " not in line:
                        log("  " + line)    # skip the per-MB progress redraws
                    if on_line:
                        on_line(line)
            if buf.strip():
                line = buf.decode("utf-8", "replace").rstrip()
                tail = (tail + [line])[-tail_lines:]
                if on_line:
                    on_line(line)
            returncode = self._proc.wait()
            log(f"  -> exit {returncode}" + (" (cancelled)" if self.cancelled else ""))
        finally:
            if self._proc.poll() is None:
                self._proc.kill()
                self._proc.wait()
            self._proc = None

        if self.cancelled:
            raise Cancelled()
        if returncode != 0:
            raise CommandFailed(argv, returncode, "\n".join(tail))
        return "\n".join(tail)
