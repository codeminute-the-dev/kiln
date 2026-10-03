"""Burn jobs: prepare, write, then read the disc back to prove it worked.

Every job runs in a worker thread and reports through
report(phase, fraction, detail). Nothing is ejected until verification has
read the disc, so a tool that claims success without writing (wodim on some
drives) cannot slip through.
"""

import os
import shutil
import subprocess
import tempfile
import time

from . import audio, burn, media
from .process import Cancelled, CommandFailed, Runner

# After finalizing, drives take a while to reload the disc. Until then they
# report an unknown status and the TOC can't be read.
SETTLE_TIMEOUT = 90
SETTLE_INTERVAL = 3


class VerifyFailed(Exception):
    pass


class PreflightError(Exception):
    pass


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    path = os.path.join(base, "kiln")
    os.makedirs(path, exist_ok=True)
    return path


def wait_for_written_disc(drive, runner, timeout=SETTLE_TIMEOUT):
    """Probe until the drive reports a disc with data, or give up."""
    deadline = time.monotonic() + timeout
    while True:
        m = media.probe(drive)
        if m.present and not m.blank and m.sessions >= 1:
            return m
        if time.monotonic() >= deadline:
            return m
        for _ in range(SETTLE_INTERVAL * 4):
            if runner.cancelled:
                raise Cancelled()
            time.sleep(0.25)


def eject(drive):
    subprocess.run(["xorriso", "-outdev", drive, "-eject", "all"],
                   capture_output=True, timeout=60)


def tree_size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- preflight


def check_writable(m, needed_bytes, need_cd=False):
    if not m.present:
        raise PreflightError("Insert a disc first.")
    if need_cd and not m.is_cd:
        raise PreflightError(f"Audio needs a CD; this is a {m.kind}.")
    if not m.writable and not m.rewritable:
        raise PreflightError("This disc is closed and can't be written. Insert a blank disc.")

    # Rewritable discs get erased as needed, so their whole capacity counts
    capacity = m.free_bytes
    if m.rewritable and not m.blank:
        capacity = 0    # unknown until erased; checked again by the burner
    if capacity and needed_bytes > capacity:
        raise PreflightError(
            f"Too much data: {needed_bytes / 2**20:.0f} MiB for "
            f"{capacity / 2**20:.0f} MiB free.")


def check_audio(m, tracks):
    if not tracks:
        raise PreflightError("Add some tracks first.")
    if len(tracks) > audio.CD_MAX_TRACKS:
        raise PreflightError(f"An audio CD holds at most {audio.CD_MAX_TRACKS} tracks.")
    seconds = sum(t.duration for t in tracks)
    if seconds > audio.CD_MAX_SECONDS:
        raise PreflightError(f"Too long: {audio.format_duration(seconds)} of music "
                             f"for an 80 minute CD.")
    sectors = sum(t.sectors for t in tracks)
    check_writable(m, 0, need_cd=True)
    if m.blank and m.free_sectors and sectors > m.free_sectors:
        minutes = m.free_sectors / media.CD_SECTORS_PER_SECOND / 60
        raise PreflightError(f"Too long for this disc ({minutes:.0f} minutes).")


# ---------------------------------------------------------------- jobs


def run_audio(drive, tracks, album_title, album_artist, speed, eject_after,
              runner: Runner, report):
    workdir = tempfile.mkdtemp(prefix="audio-", dir=_cache_dir())
    try:
        wavs = []
        for i, track in enumerate(tracks, 1):
            dest = os.path.join(workdir, f"track{i:02d}.wav")

            def progress(f, i=i):
                report("Preparing tracks", ((i - 1) + f) / len(tracks),
                       f"Converting track {i} of {len(tracks)}: {track.title}")

            audio.convert_to_cd_wav(track.path, dest, progress,
                                    is_cancelled=lambda: runner.cancelled)
            wavs.append(dest)

        sheet = os.path.join(workdir, "cdtext.v07t")
        with open(sheet, "wb") as f:
            f.write(audio.make_v07t_sheet(album_title, album_artist, tracks))

        progress = burn.CdrskinProgress([os.path.getsize(w) for w in wavs])

        def on_line(line):
            if progress.feed(line):
                detail = f"Track {progress.track} of {len(tracks)}" if progress.track else ""
                if progress.speed:
                    detail += f" at {progress.speed:g}x"
                report(progress.phase, progress.fraction, detail)

        report("Starting", 0.0, "Waiting for the drive")
        runner.run(burn.audio_command(drive, wavs, sheet, speed, eject=False), on_line)

        report("Verifying", None, "Reading the disc back")
        verify_audio(drive, len(tracks), runner)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if eject_after:
        eject(drive)
    return f"Audio CD written and verified: {len(tracks)} tracks."


def run_data(drive, items, label, speed, eject_after, runner: Runner, report):
    progress = burn.XorrisoProgress()

    def on_line(line):
        if progress.feed(line):
            detail = f"{progress.speed:g}x" if progress.speed else ""
            report(progress.phase, progress.fraction, detail)

    report("Starting", 0.0, "Waiting for the drive")
    runner.run(burn.data_command(drive, items, label, speed, eject=False), on_line)

    report("Verifying", None, "Reading the disc back")
    m = wait_for_written_disc(drive, runner)
    if not m.present or m.blank or m.sessions < 1:
        raise VerifyFailed("The burner reported success, but the disc still looks empty.")

    if eject_after:
        eject(drive)
    return f"Data disc “{burn.volume_label(label)}” written and verified."


def run_image(drive, image, speed, eject_after, runner: Runner, report):
    progress = burn.CdrskinProgress([os.path.getsize(image)])

    def on_line(line):
        if progress.feed(line):
            detail = f"{progress.speed:g}x" if progress.speed else ""
            report(progress.phase, progress.fraction, detail)

    report("Starting", 0.0, "Waiting for the drive")
    runner.run(burn.image_command(drive, image, speed, eject=False), on_line)

    report("Verifying", None, "Reading the disc back")
    m = wait_for_written_disc(drive, runner)
    if not m.present or m.blank or m.sessions < 1:
        raise VerifyFailed("The burner reported success, but the disc still looks empty.")

    if eject_after:
        eject(drive)
    return f"{os.path.basename(image)} written and verified."


def run_blank(drive, full, runner: Runner, report):
    m = media.probe(drive)
    if not m.present:
        raise PreflightError("Insert a disc first.")
    if not m.rewritable:
        raise PreflightError(f"A {m.kind} can't be erased; only rewritable discs can.")

    progress = burn.XorrisoProgress()
    progress.phase = "Erasing"

    def on_line(line):
        if progress.feed(line):
            report("Erasing", progress.fraction, "")

    report("Erasing", None, "Waiting for the drive")
    runner.run(burn.blank_command(drive, full), on_line)

    m = media.probe(drive)
    if not m.blank:
        raise VerifyFailed("Erase finished, but the disc doesn't read as blank.")
    return "Disc erased."


def verify_audio(drive, expected_tracks, runner):
    m = wait_for_written_disc(drive, runner)
    if not m.present or m.blank:
        raise VerifyFailed("The burner reported success, but the disc is still blank.")

    found, deadline = 0, time.monotonic() + SETTLE_TIMEOUT
    while True:
        try:
            found = burn.parse_toc_tracks(Runner().run(burn.toc_command(drive)))
        except CommandFailed:
            found = 0       # drive not ready yet
        if found == expected_tracks or time.monotonic() >= deadline:
            break
        for _ in range(SETTLE_INTERVAL * 4):
            if runner.cancelled:
                raise Cancelled()
            time.sleep(0.25)
    if found != expected_tracks:
        raise VerifyFailed(f"Expected {expected_tracks} tracks on the disc, found {found}.")
