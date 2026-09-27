import unittest
from unittest import mock

import apple_music_monitor
from apple_music_monitor import Playback, matches, parse_playback


class AppleMusicMonitorTests(unittest.TestCase):
    def test_parse_playback_reads_track_and_position(self):
        playback = parse_playback("playing\x1fSong\x1fArtist\x1fABC123\x1f12.5\n")

        self.assertEqual(
            playback,
            Playback("playing", "Song", "Artist", "ABC123", 12.5),
        )

    def test_song_matching_is_exact_case_insensitive_and_space_tolerant(self):
        playback = Playback("playing", "  My   Song ", "The Artist")

        self.assertTrue(matches(playback, "my song"))
        self.assertTrue(matches(playback, "MY SONG", "the artist"))
        self.assertFalse(matches(playback, "My Song (Remix)"))
        self.assertFalse(matches(playback, "My Song", "Another Artist"))

    def test_nonplaying_or_missing_track_output_is_safe(self):
        self.assertEqual(parse_playback("not_running\n"), Playback("not_running"))
        self.assertEqual(parse_playback("paused\n"), Playback("paused"))

    def test_monitor_notifies_once_then_again_when_same_track_restarts(self):
        samples = iter(
            [
                Playback("playing", "Song", "Artist", "ID", 10),
                Playback("paused", "Song", "Artist", "ID", 10),
                Playback("playing", "Song", "Artist", "ID", 11),
                Playback("playing", "Song", "Artist", "ID", 0),
            ]
        )
        notifications = []

        with (
            mock.patch.object(
                apple_music_monitor, "read_playback", side_effect=lambda _path: next(samples)
            ),
            mock.patch.object(
                apple_music_monitor,
                "send_notification",
                side_effect=lambda _path, playback: notifications.append(playback),
            ),
            mock.patch.object(
                apple_music_monitor.time,
                "sleep",
                side_effect=[None, None, None, KeyboardInterrupt],
            ),
            mock.patch("builtins.print"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                apple_music_monitor.monitor("Song", "Artist", 1.0, "osascript")

        self.assertEqual(len(notifications), 2)


if __name__ == "__main__":
    unittest.main()
