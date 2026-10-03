"""Audio CD preparation: tag/duration probing, conversion to CD audio, CD-TEXT."""

import os
from dataclasses import dataclass

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import GLib, Gst, GstPbutils  # noqa: E402

from .media import CD_SECTORS_PER_SECOND  # noqa: E402

Gst.init(None)

CD_MAX_SECONDS = 80 * 60
CD_MAX_TRACKS = 99
CDTEXT_FIELD_MAX = 160      # keeps the whole text block well inside CD-TEXT limits


@dataclass
class Track:
    path: str
    title: str = ""
    artist: str = ""
    duration: float = 0.0   # seconds

    @property
    def sectors(self):
        return int(round(self.duration * CD_SECTORS_PER_SECOND))


def _tag(tags, name):
    if tags is None:
        return ""
    ok, value = tags.get_string(name)
    return value.strip() if ok and value else ""


def probe(path, timeout=10):
    """Read duration and tags. Raises ValueError for files without audio."""
    discoverer = GstPbutils.Discoverer.new(timeout * Gst.SECOND)
    try:
        info = discoverer.discover_uri(Gst.filename_to_uri(os.path.abspath(path)))
    except GLib.Error as e:
        raise ValueError(f"{os.path.basename(path)}: {e.message}") from None
    if not info.get_audio_streams():
        raise ValueError(f"{os.path.basename(path)} has no audio")

    tags = info.get_tags()
    title = _tag(tags, Gst.TAG_TITLE)
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]
    return Track(path=path,
                 title=title,
                 artist=_tag(tags, Gst.TAG_ARTIST),
                 duration=info.get_duration() / Gst.SECOND)


def convert_to_cd_wav(src, dest, on_progress=None, is_cancelled=None):
    """Decode any GStreamer-readable file to 44.1 kHz 16-bit stereo WAV."""
    pipeline = Gst.parse_launch(
        "filesrc name=src ! decodebin ! audioconvert ! audioresample ! "
        "audio/x-raw,format=S16LE,rate=44100,channels=2,layout=interleaved ! "
        "wavenc ! filesink name=sink")
    pipeline.get_by_name("src").set_property("location", src)
    pipeline.get_by_name("sink").set_property("location", dest)

    bus = pipeline.get_bus()
    pipeline.set_state(Gst.State.PLAYING)
    try:
        while True:
            msg = bus.timed_pop_filtered(250 * Gst.MSECOND,
                                         Gst.MessageType.EOS | Gst.MessageType.ERROR)
            if is_cancelled and is_cancelled():
                from .process import Cancelled
                raise Cancelled()
            if msg is None:
                if on_progress:
                    ok_pos, pos = pipeline.query_position(Gst.Format.TIME)
                    ok_dur, dur = pipeline.query_duration(Gst.Format.TIME)
                    if ok_pos and ok_dur and dur > 0:
                        on_progress(min(pos / dur, 1.0))
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                raise RuntimeError(f"Could not convert {os.path.basename(src)}: {err.message}")
            break
    finally:
        pipeline.set_state(Gst.State.NULL)
    if on_progress:
        on_progress(1.0)


def _latin1(text):
    """CD-TEXT block 0 is ISO-8859-1; replace what cannot be represented."""
    text = " ".join((text or "").split())[:CDTEXT_FIELD_MAX]
    return "".join(c if ord(c) < 256 else "_" for c in text)


def make_v07t_sheet(album_title, album_artist, tracks):
    """CD-TEXT input sheet in the Sony v07t format that libburn/cdrskin read."""
    lines = [
        "Input Sheet Version = 0.7T",
        "Text Code           = 8859",
        "Language Code       = English",
        f"Album Title         = {_latin1(album_title)}",
        f"Artist Name         = {_latin1(album_artist)}",
        "Text Data Copy Protection = OFF",
        "First Track Number  = 1",
        f"Last Track Number   = {len(tracks)}",
    ]
    for i, track in enumerate(tracks, 1):
        lines.append(f"Track {i:02d} Title      = {_latin1(track.title)}")
        lines.append(f"Track {i:02d} Artist     = {_latin1(track.artist)}")
    return ("\n".join(lines) + "\n").encode("latin-1")


def format_duration(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"
