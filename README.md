# Kiln

A small GTK4 disc burner for Linux. Burns audio CDs (with CD-TEXT), data discs and ISO images, and checks the disc afterwards so a burn that silently wrote nothing gets reported as a failure instead of a success.

> **Status: early alpha.** Audio CD burning has worked end to end on real hardware, but there is an open bug where erasing and some burns fail with a non-zero exit status from the burn tools. Expect rough edges.

## Why

Kiln started after Brasero kept failing on an Optiarc AD-5240S drive:

- its `wodim` backend reported successful burns at impossible speeds while the disc stayed blank
- it kept treating a burned, closed CD-RW as blank, so it never offered to erase it before a reburn

Kiln is built around two rules that come from that:

1. **Never trust a cached disc state.** Every check asks the drive directly through libburn.
2. **Never trust "success".** After every burn Kiln reads the disc back (sessions, and for audio the exact track count) before ejecting.

## Features

- **Audio CD**: drop in any format GStreamer can decode (FLAC, MP3, Ogg, WAV, ...). Titles and artists are read from tags and written as CD-TEXT. Tracks can be renamed, reordered and removed; a capacity bar shows minutes used against the real disc.
- **Data disc**: files and folders, ISO 9660 with Rock Ridge and Joliet, custom disc name.
- **Disc image**: burn `.iso` / `.img` files.
- **Erase**: quick or full, only offered for rewritable discs.
- Pre-flight checks: disc present, writable, fits, 80 minute / 99 track limits, audio only on CDs.
- Rewritable discs that already have data are erased automatically before burning.
- Blocks suspend and logout while a burn is running.
- Every command and its output is logged to `~/.cache/kiln/kiln.log`.

## Requirements

- Python 3.10+
- GTK 4 and libadwaita 1.5+ with PyGObject
- GStreamer 1.0 with the base and good plugins (for decoding audio)
- `xorriso` and `cdrskin` (both from the libburnia project)

On Ubuntu / Debian:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
  gstreamer1.0-plugins-good xorriso cdrskin
```

Your user needs access to the drive, which on most distros means being in the `cdrom` group.

## Running

```sh
git clone https://github.com/codeminute-the-dev/kiln.git
cd kiln
bin/kiln
```

To install it for your user, link the launcher and desktop entry:

```sh
ln -s "$PWD/bin/kiln" ~/.local/bin/kiln
cp data/dev.codeminute.Kiln.desktop ~/.local/share/applications/
```

Files can be opened directly: `kiln *.flac` starts an audio CD, `kiln image.iso` opens the image page.

## How it works

| Job | Tool |
| --- | --- |
| Drive list and disc status | `xorriso -devices`, `xorriso -toc` |
| Audio CD + CD-TEXT | `cdrskin -sao -audio` with a v07t CD-TEXT sheet |
| ISO image | `cdrskin -data` |
| Data disc | `xorriso -map ... -commit` |
| Erase | `xorriso -blank` |
| Audio verification | `cdrskin -toc` |

Audio is decoded with GStreamer to 44.1 kHz / 16-bit / stereo WAV before burning. CD-TEXT is written in ISO-8859-1; characters outside it become `_`.

## Tests

```sh
python3 -m unittest discover -s tests
```

The parser tests use output captured from real drives.

## License

Copyright (C) 2026 Codeminute

Kiln is free software, licensed under the [GNU General Public License v3.0 or later](LICENSE). You can use, modify and share it; if you distribute it or a modified version, it has to stay under the GPL with its source code available.
