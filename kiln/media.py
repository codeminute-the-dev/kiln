"""Drive and disc inspection.

Everything here asks libburn (through xorriso) for the current state of the
drive. Nothing is cached: Brasero's habit of trusting a stale probe is what
made it treat a closed disc as blank.
"""

import re
import subprocess
from dataclasses import dataclass, field

SECTOR = 2048
CD_AUDIO_SECTOR = 2352
CD_SECTORS_PER_SECOND = 75


@dataclass
class Drive:
    path: str
    vendor: str
    model: str

    @property
    def name(self):
        return f"{self.vendor} {self.model}".strip()


@dataclass
class Media:
    present: bool = False
    kind: str = ""              # "CD-RW", "DVD+R", ...
    product: str = ""
    blank: bool = False
    appendable: bool = False
    closed: bool = False
    sessions: int = 0
    free_sectors: int = 0
    write_speeds: list = field(default_factory=list)   # e.g. [10.0, 4.0], fastest first
    raw: str = ""

    @property
    def rewritable(self):
        return self.kind.endswith("RW") or self.kind in ("BD-RE", "DVD-RAM")

    @property
    def is_cd(self):
        return self.kind.startswith("CD")

    @property
    def free_bytes(self):
        return self.free_sectors * SECTOR

    @property
    def writable(self):
        return self.present and (self.blank or self.appendable)

    def describe(self):
        if not self.present:
            return "No disc"
        if self.blank:
            state = "blank"
        elif self.appendable:
            state = "appendable"
        elif self.closed:
            state = "closed"
        else:
            state = "written"
        free = f", {self.free_bytes / 2**20:.0f} MiB free" if self.free_sectors else ""
        return f"{self.kind}, {state}{free}"


_DEV_RE = re.compile(r"^\s*\d+\s+-dev\s+'([^']+)'\s+\S+\s+:\s+'([^']*)'\s+'([^']*)'")


def parse_devices(text):
    drives = []
    for line in text.splitlines():
        m = _DEV_RE.match(line)
        if m:
            drives.append(Drive(m.group(1), m.group(2).strip(), m.group(3).strip()))
    return drives


def parse_media(text):
    media = Media(raw=text)
    speeds = []
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()

        if key == "Media current":
            if value and "not recognizable" not in value and "is not present" not in value:
                media.present = True
                media.kind = value.split()[0]
        elif key == "Media product":
            media.product = value
        elif key == "Media status":
            media.blank = "is blank" in value
            media.appendable = "is appendable" in value
            media.closed = "is closed" in value
        elif key == "Media summary":
            m = re.match(r"(\d+)\s+session", value)
            if m:
                media.sessions = int(m.group(1))
        elif key == "Media space":
            m = re.match(r"(\d+)s", value)
            if m:
                media.free_sectors = int(m.group(1))
        elif key == "Media blocks" and not media.free_sectors:
            m = re.search(r"(\d+)\s+writable", value)
            if m:
                media.free_sectors = int(m.group(1))
        elif key.startswith("Write speed"):
            m = re.search(r"([\d.]+)x", value)
            if m:
                speeds.append(float(m.group(1)))

    if not media.present:
        media.blank = media.appendable = media.closed = False
        media.free_sectors = 0
    media.write_speeds = sorted(set(speeds), reverse=True)
    return media


def _xorriso(*args, timeout=60):
    result = subprocess.run(["xorriso", *args], capture_output=True, text=True,
                            errors="replace", timeout=timeout)
    out = result.stdout + result.stderr
    from .process import log
    status = [line for line in out.splitlines()
              if line.startswith(("Media current", "Media status", "Media summary"))
              or "FAILURE" in line or "SORRY" in line]
    log(f"probe xorriso {' '.join(args)} -> exit {result.returncode}\n  " + "\n  ".join(status))
    return out


def list_drives():
    return parse_devices(_xorriso("-devices"))


def probe(drive_path):
    return parse_media(_xorriso("-outdev", drive_path, "-toc",
                                "-list_speeds", "-tell_media_space"))
