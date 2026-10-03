"""Command lines for cdrskin/xorriso and parsers for their progress output."""

import os
import re

# ---------------------------------------------------------------- commands


def _speed_arg(speed):
    """speed is a CD/DVD "x" factor or None for the drive's maximum."""
    return [f"speed={speed:g}"] if speed else []


def audio_command(drive, wavs, sheet_path, speed=None, eject=True):
    argv = ["cdrskin", "-v", f"dev={drive}", "gracetime=0", "blank=as_needed",
            *_speed_arg(speed), "-sao"]
    if sheet_path:
        argv.append(f"input_sheet_v07t={sheet_path}")
    argv += ["-audio", *wavs]
    if eject:
        argv.append("-eject")
    return argv


def image_command(drive, image, speed=None, eject=True):
    argv = ["cdrskin", "-v", f"dev={drive}", "gracetime=0", "blank=as_needed",
            *_speed_arg(speed), "-data", image]
    if eject:
        argv.append("-eject")
    return argv


def blank_command(drive, full=False):
    return ["xorriso", "-report_about", "UPDATE", "-outdev", drive,
            "-blank", "all" if full else "fast"]


def iso_path_for(name, used):
    """Disc path for a top-level item; renames duplicates like 'a (2)'."""
    base = os.path.basename(os.path.normpath(name)) or "untitled"
    candidate, n = base, 2
    while candidate.lower() in used:
        stem, ext = os.path.splitext(base)
        candidate = f"{stem} ({n}){ext}"
        n += 1
    used.add(candidate.lower())
    return "/" + candidate


def volume_label(text):
    """ISO 9660 volume ids are at most 32 characters."""
    label = "".join(c for c in (text or "") if c.isprintable()).strip()
    return (label or "Data disc")[:32]


def data_command(drive, items, label, speed=None, eject=True):
    argv = ["xorriso", "-report_about", "UPDATE", "-return_with", "FAILURE", "32",
            "-outdev", drive, "-blank", "as_needed",
            "-volid", volume_label(label), "-joliet", "on"]
    if speed:
        argv += ["-speed", f"{speed:g}"]
    used = set()
    for item in items:
        argv += ["-map", item, iso_path_for(item, used)]
    argv += ["-close", "on", "-commit"]
    if eject:
        argv += ["-eject", "all"]
    return argv


def toc_command(drive):
    return ["cdrskin", f"dev={drive}", "-toc"]


# ---------------------------------------------------------------- progress

_CDRSKIN_TRACK = re.compile(
    r"Track\s+(\d+):\s+(\d+)\s+of\s+(\d+)\s+MB written(?:.*?([\d.]+)x)?")
_XORRISO_PERCENT = re.compile(r"UPDATE\s*:.*?([\d.]+)%")
_SPEED = re.compile(r"([\d.]+)x[CDB]?\b")


class CdrskinProgress:
    """Turns per-track 'Track 03: 12 of 61 MB written' lines into 0..1 overall.

    track_sizes are the byte sizes of the tracks, so tracks of different
    lengths are weighted correctly.
    """

    def __init__(self, track_sizes):
        self.sizes = [max(int(s), 1) for s in track_sizes] or [1]
        self.total = sum(self.sizes)
        self.fraction = 0.0
        self.track = 0
        self.speed = None
        self.phase = "Writing"

    def feed(self, line):
        """Returns True when the line changed the reported state."""
        m = _CDRSKIN_TRACK.search(line)
        if m:
            track, done, size = int(m.group(1)), int(m.group(2)), int(m.group(3))
            before = sum(self.sizes[:track - 1])
            current = self.sizes[track - 1] if track <= len(self.sizes) else 0
            part = done / size if size else 0
            self.fraction = min((before + current * part) / self.total, 1.0)
            self.track = track
            if m.group(4):
                self.speed = float(m.group(4))
            self.phase = "Writing"
            return True
        if "working pre-track" in line:
            # lead-in, which also carries the CD-TEXT
            self.phase = "Writing lead-in"
            return True
        if "thank you for being patient" in line or "Fixating" in line:
            self.phase = "Finalizing"
            self.fraction = 1.0
            return True
        if "blanking" in line.lower() and "burn_disc_blank" not in line:
            self.phase = "Erasing"
            return True
        return False


class XorrisoProgress:
    def __init__(self):
        self.fraction = 0.0
        self.speed = None
        self.phase = "Writing"

    def feed(self, line):
        if "UPDATE" not in line:
            if "Writing to" in line or "Beginning to write" in line:
                self.phase = "Writing"
                return True
            return False
        lowered = line.lower()
        if "blank" in lowered:
            self.phase = "Erasing"
        elif "format" in lowered:
            self.phase = "Formatting"
        elif "closing" in lowered or "fixat" in lowered:
            self.phase = "Finalizing"
        else:
            self.phase = "Writing"
        m = _XORRISO_PERCENT.search(line)
        if m:
            self.fraction = min(float(m.group(1)) / 100.0, 1.0)
        s = _SPEED.search(line.split("buf", 1)[-1])
        if s:
            self.speed = float(s.group(1))
        return True


def parse_toc_tracks(text):
    """Count tracks in `cdrskin -toc` output (the lead-out is not numbered)."""
    return sum(1 for line in text.splitlines() if re.match(r"\s*track:\s*\d+\s", line))
