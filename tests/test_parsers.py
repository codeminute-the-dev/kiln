import unittest

from kiln import audio, burn, media

BLANK_CDRW = """\
Drive current: -outdev '/dev/sr0'
Media current: CD-RW
Media product: 97m27s29f/79m59s74f , Princo Corporation
Media status : is blank
Media blocks : 0 readable , 359847 writable , 359847 overall
Media summary: 0 sessions, 0 data blocks, 0 data,  703m free
Write speed  :   1764k , 10.0xC
Write speed l:    706k ,  4.0xC
Write speed L:    706k ,  4.0xC
Write speed H:   1764k , 10.0xC
Media space  : 359847s
"""

CLOSED_CDRW = """\
Media current: CD-RW
Media status : is written , is closed
Media summary: 1 session, 0 data blocks,     0 data,     0 free
"""

NO_DISC = """\
Media current: is not recognizable
Media status : is not recognizable
"""

DEVICES = """\
Full drive scan done
-----------------------------------------------------------------------------
0  -dev '/dev/sr0' rwrw-- :  'Optiarc ' 'DVD RW AD-5240S'
1  -dev '/dev/sr1' rwrw-- :  'HL-DT-ST' 'BD-RE  WH16NS40'
-----------------------------------------------------------------------------
"""


class MediaTests(unittest.TestCase):
    def test_devices(self):
        drives = media.parse_devices(DEVICES)
        self.assertEqual([d.path for d in drives], ["/dev/sr0", "/dev/sr1"])
        self.assertEqual(drives[0].name, "Optiarc DVD RW AD-5240S")

    def test_blank_cdrw(self):
        m = media.parse_media(BLANK_CDRW)
        self.assertTrue(m.present and m.blank and m.writable and m.rewritable and m.is_cd)
        self.assertEqual(m.free_sectors, 359847)
        self.assertEqual(m.write_speeds, [10.0, 4.0])
        self.assertEqual(m.describe(), "CD-RW, blank, 703 MiB free")

    def test_closed_cdrw_is_not_blank(self):
        # The case Brasero got wrong: a burned, closed CD-RW
        m = media.parse_media(CLOSED_CDRW)
        self.assertTrue(m.present and m.closed)
        self.assertFalse(m.blank or m.writable)
        self.assertEqual(m.sessions, 1)

    def test_only_rewritable_discs_can_be_erased(self):
        for kind, rw in (("CD-RW", True), ("DVD+RW", True), ("DVD-RW", True), ("BD-RE", True),
                         ("DVD-RAM", True), ("CD-R", False), ("DVD+R", False), ("DVD-R", False),
                         ("CD-ROM", False), ("DVD-ROM", False), ("BD-R", False)):
            m = media.parse_media(f"Media current: {kind}\nMedia status : is written , is closed\n")
            self.assertEqual(m.rewritable, rw, kind)

    def test_no_disc(self):
        m = media.parse_media(NO_DISC)
        self.assertFalse(m.present)
        self.assertEqual(m.describe(), "No disc")


class ProgressTests(unittest.TestCase):
    def test_cdrskin_weights_tracks_by_size(self):
        p = burn.CdrskinProgress([30, 10])
        p.feed("Track 01:   15 of   30 MB written (fifo 100%) [buf  99%]  10.0x.")
        self.assertAlmostEqual(p.fraction, 15 / 40)
        self.assertEqual(p.speed, 10.0)
        p.feed("Track 02:    5 of   10 MB written (fifo 100%) [buf  99%]   9.8x.")
        self.assertAlmostEqual(p.fraction, 35 / 40)
        self.assertEqual(p.track, 2)
        p.feed("Fixating...")
        self.assertEqual(p.phase, "Finalizing")

    def test_cdrskin_real_output(self):
        # captured from a -dummy run on the Optiarc AD-5240S
        p = burn.CdrskinProgress([3528000, 3528000])
        p.feed("cdrskin: working pre-track (burning since 3 seconds)         ")
        self.assertEqual(p.phase, "Writing lead-in")
        p.feed("Track 02:    1 of    3 MB written (fifo 100%) [buf 100%]   9.8x.")
        self.assertEqual((p.track, p.speed, p.phase), (2, 9.8, "Writing"))
        p.feed("cdrskin: thank you for being patient for 20 seconds                     ")
        self.assertEqual((p.phase, p.fraction), ("Finalizing", 1.0))

    def test_cdrskin_ignores_noise(self):
        p = burn.CdrskinProgress([10])
        self.assertFalse(p.feed("cdrskin: status 1 burn_disc_blank \"The drive holds a blank disc\""))

    def test_xorriso_writing(self):
        p = burn.XorrisoProgress()
        p.feed("xorriso : UPDATE :   34567s   25.3%   fifo 100%  buf 100%    4.0xC")
        self.assertAlmostEqual(p.fraction, 0.253)
        self.assertEqual(p.speed, 4.0)
        self.assertEqual(p.phase, "Writing")

    def test_xorriso_blanking(self):
        p = burn.XorrisoProgress()
        p.feed("xorriso : UPDATE : Blanking  ( 42.0% done in 12 seconds )")
        self.assertEqual(p.phase, "Erasing")
        self.assertAlmostEqual(p.fraction, 0.42)


class CommandTests(unittest.TestCase):
    def test_duplicate_names_get_renamed(self):
        used = set()
        self.assertEqual(burn.iso_path_for("/a/photos", used), "/photos")
        self.assertEqual(burn.iso_path_for("/b/photos", used), "/photos (2)")
        self.assertEqual(burn.iso_path_for("/c/Photos", used), "/Photos (3)")

    def test_volume_label(self):
        self.assertEqual(burn.volume_label(""), "Data disc")
        self.assertEqual(len(burn.volume_label("x" * 50)), 32)

    def test_audio_command_does_not_eject_before_verify(self):
        argv = burn.audio_command("/dev/sr0", ["a.wav"], "s.v07t", speed=4, eject=False)
        self.assertNotIn("-eject", argv)
        self.assertIn("speed=4", argv)
        self.assertIn("input_sheet_v07t=s.v07t", argv)

    def test_toc_tracks(self):
        toc = ("first: 1 last 2\n"
               "track:   1 lba:         0 (        0) 00:02:00 adr: 1 control: 0 mode: 0 (audio)\n"
               "track:   2 lba:     13800 (    55200) 03:06:00 adr: 1 control: 0 mode: 0 (audio)\n"
               "track:lout lba:     30000 (   120000) 06:42:00 adr: 1 control: 0 mode: -1\n")
        self.assertEqual(burn.parse_toc_tracks(toc), 2)


class CdTextTests(unittest.TestCase):
    def test_sheet_is_latin1_with_fallback(self):
        tracks = [audio.Track("a", "Café", "Björk ☃", 10), audio.Track("b", "Two", "", 10)]
        sheet = audio.make_v07t_sheet("Album", "", tracks).decode("latin-1")
        self.assertIn("Track 01 Title      = Café", sheet)
        self.assertIn("Track 01 Artist     = Björk _", sheet)
        self.assertIn("Last Track Number   = 2", sheet)


if __name__ == "__main__":
    unittest.main()
