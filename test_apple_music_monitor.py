import unittest
from unittest import mock

import apple_music_monitor
from apple_music_monitor import Playback, matches, parse_playback


class AppleMusicMonitorTests(unittest.TestCase):
    def test_parse_playback_reads_track_and_position(self):
        playback = parse_playback(
            "playing\x1fdrivers license\x1fOlivia Rodrigo\x1fABC123\x1f12.5\n"
        )

        self.assertEqual(
            playback,
            Playback("playing", "drivers license", "Olivia Rodrigo", "ABC123", 12.5),
        )

    def test_target_matching_is_exact_case_insensitive_and_space_tolerant(self):
        self.assertTrue(matches(Playback("playing", " Drivers  License ", "OLIVIA RODRIGO")))
        self.assertFalse(matches(Playback("playing", "drivers license (live)", "Olivia Rodrigo")))
        self.assertFalse(matches(Playback("playing", "drivers license", "Another Artist")))

    def test_nonplaying_or_missing_track_output_is_safe(self):
        self.assertEqual(parse_playback("stopped\n"), Playback("stopped"))
        self.assertEqual(parse_playback("paused\n"), Playback("paused"))

    @mock.patch.object(apple_music_monitor, "run_osascript")
    @mock.patch.object(apple_music_monitor.subprocess, "run")
    def test_station_is_muted_before_it_is_opened(self, run, run_osascript):
        run_osascript.side_effect = ["37", ""]
        run.return_value = mock.Mock(returncode=0, stderr="")

        old_volume = apple_music_monitor.start_silent_hits("osascript", "open")

        self.assertEqual(old_volume, 37)
        self.assertEqual(
            run_osascript.call_args_list[1].args[1],
            apple_music_monitor.MUTE_MUSIC_SCRIPT,
        )
        run.assert_called_once_with(
            ["open", apple_music_monitor.STATION_URL],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_monitor_notifies_once_and_always_restores_volume(self):
        samples = iter(
            [
                Playback("playing", "drivers license", "Olivia Rodrigo", "ID", 10),
                Playback("playing", "drivers license", "Olivia Rodrigo", "ID", 11),
            ]
        )

        with (
            mock.patch.object(apple_music_monitor, "start_silent_hits", return_value=42),
            mock.patch.object(
                apple_music_monitor, "read_playback", side_effect=lambda _path: next(samples)
            ),
            mock.patch.object(apple_music_monitor, "send_notification") as notify,
            mock.patch.object(
                apple_music_monitor.time,
                "sleep",
                side_effect=[None, KeyboardInterrupt],
            ),
            mock.patch.object(apple_music_monitor, "stop_and_restore") as restore,
            mock.patch("builtins.print"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                apple_music_monitor.monitor(1.0, "osascript", "open")

        self.assertEqual(notify.call_count, 1)
        restore.assert_called_once_with("osascript", 42)


if __name__ == "__main__":
    unittest.main()
