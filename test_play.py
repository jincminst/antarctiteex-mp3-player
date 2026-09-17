import ast
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import play
from rich.style import Style


class FakeListWindow:
    def __init__(self, height):
        self.height = height

    def getmaxyx(self):
        return self.height, 80


class FakeScreen:
    def __init__(self, height=30, width=100):
        self.height = height
        self.width = width
        self.subwins = []

    def getmaxyx(self):
        return self.height, self.width

    def subwin(self, height, width, y, x):
        window = mock.Mock()
        window.geometry = (height, width, y, x)
        self.subwins.append(window)
        return window


def bare_player(*, visible=4):
    player = play.Player.__new__(play.Player)
    player.win_lst = FakeListWindow(visible + 3)
    player.apple_radio_enabled = False
    player.youtube_preview_enabled = False
    player.youtube_results = []
    player.songs = []
    player.scroll = 0
    player.dirty = False
    player._needs_redraw_lst = False
    return player


class FunctionInventoryTests(unittest.TestCase):
    def test_help_continuation_lines_align_with_descriptions(self):
        width = 38
        lines = play.HelpScreen._wrapped_help_text(width).splitlines()
        continuation_lines = [line for line in lines if line.startswith(" " * 18)]

        self.assertTrue(continuation_lines)
        self.assertTrue(all(len(line) <= width for line in lines))
        self.assertTrue(
            all(len(line) == 18 or line[18] != " " for line in continuation_lines)
        )


    def test_playlist_tab_has_visible_dividers(self):
        player = play.Player.__new__(play.Player)
        player._safe = mock.Mock()

        width = player._merged_tab(mock.Mock(), 0, 4, "Favorites", first=True)

        self.assertEqual(width, len(" Favorites ") + 2)
        self.assertTrue(player._safe.call_args_list[0].args[3].startswith("┌"))
        self.assertEqual(player._safe.call_args_list[-1].args[3], "│")

    def test_header_button_is_square_and_not_underlined(self):
        player = play.Player.__new__(play.Player)
        player._safe = mock.Mock()

        width = player._outline_btn(mock.Mock(), 2, 10, "PLAY")

        self.assertEqual(width, len(" PLAY ") + 2)
        self.assertTrue(player._safe.call_args_list[0].args[3].startswith("┌"))
        self.assertTrue(player._safe.call_args_list[-1].args[3].startswith("└"))

    def test_every_function_compiles_and_player_methods_are_callable(self):
        source = Path(play.__file__).read_text()
        tree = ast.parse(source, play.__file__)
        functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertGreater(len(functions), 190)
        compile(tree, play.__file__, "exec")
        for name, member in inspect.getmembers(play.Player):
            if name.startswith("__") and name not in {"__init__"}:
                continue
            if inspect.isfunction(member) or isinstance(member, staticmethod):
                self.assertTrue(callable(member), name)

    def test_playlist_tabs_sit_directly_on_library_panel(self):
        player = play.Player.__new__(play.Player)
        player.stdscr = FakeScreen()
        player.apple_radio_enabled = False
        player.youtube_preview_enabled = False
        player.rename_active = False

        player._layout()

        tab_geometry = player.stdscr.subwins[2].geometry
        list_geometry = player.stdscr.subwins[3].geometry
        self.assertEqual(tab_geometry[2], list_geometry[2])
        self.assertEqual(tab_geometry[3] + tab_geometry[1] - 1, list_geometry[3])

    def test_playlist_rename_row_stays_attached_above_library(self):
        player = play.Player.__new__(play.Player)
        player.stdscr = FakeScreen()
        player.apple_radio_enabled = False
        player.youtube_preview_enabled = False
        player.rename_active = True

        player._layout()

        tab_geometry = player.stdscr.subwins[2].geometry
        list_geometry = player.stdscr.subwins[3].geometry
        self.assertEqual(tab_geometry[2], list_geometry[2])
        self.assertEqual(tab_geometry[3] + tab_geometry[1] - 1, list_geometry[3])

    def test_short_terminal_uses_compact_layout(self):
        player = play.Player.__new__(play.Player)
        player.stdscr = FakeScreen(height=20)
        player.apple_radio_enabled = False
        player.youtube_preview_enabled = False
        player.rename_active = False

        player._layout()

        self.assertEqual(player.stdscr.subwins[0].geometry[0], 7)
        self.assertEqual(player.stdscr.subwins[2].geometry[0], 8)


class InitializationIntegrationTests(unittest.TestCase):
    def test_restored_shuffle_song_has_a_playback_queue_without_refresh(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "Track.mp3").touch()
            Path(folder, play.CACHE_FILE).write_text(json.dumps({
                "songs": {},
                "playlists": {"Mix": ["Track"]},
                "settings": {"session": {
                    "song": "Track", "playlist": "Mix", "play_mode": "shuffle",
                }},
            }))
            with (
                mock.patch.object(play.Player, "_restore_saved_session", return_value=True),
                mock.patch.object(play.Player, "_start_duration_loader"),
                mock.patch.object(play.Player, "_watch_playback"),
            ):
                player = play.Player(folder=folder)
            try:
                self.assertEqual(player.play_mode, "shuffle")
                self.assertEqual(player.play_tab, "Mix")
                self.assertEqual(player.play_pool, ["Track"])
            finally:
                player._shutdown_audio()

    def test_canceling_radio_restores_local_song_and_position(self):
        with tempfile.TemporaryDirectory() as folder:
            player = play.Player(folder=folder)
            player.current = "song.mp3"
            player._all_songs_set = {"song.mp3"}
            player.all_songs = ["song.mp3"]
            player.playlists = {"Mix": ["song.mp3"]}
            player.playlist_tags = {}
            player.tab_names = ["All", "Mix"]
            player.active_tab = 1
            player.play_tab = "Mix"
            player.paused = False
            station = {"name": "Apple Music 1", "url": ""}

            with (
                mock.patch.object(
                    player, "_current_pos_in_song", return_value=(4321, 9000)
                ),
                mock.patch.object(player, "_stop_apple_radio_audio"),
                mock.patch.object(player, "_rebuild"),
                mock.patch.object(player, "_layout"),
                mock.patch.object(player, "_update_timeout"),
                mock.patch.object(player, "_cached_duration_ms", return_value=9000),
                mock.patch.object(player, "_restart_current_audio") as restart,
                mock.patch.object(play.pygame, "mixer"),
            ):
                player._play_apple_radio_station(station)
                self.assertEqual(player._pre_radio_song, "song.mp3")
                self.assertEqual(player._pre_radio_pos_ms, 4321)
                player._exit_apple_radio_mode()

            self.assertEqual(player.current, "song.mp3")
            self.assertEqual(player.play_tab, "Mix")
            self.assertEqual(player.play_pool, ["song.mp3"])
            self.assertFalse(player.apple_radio_enabled)
            restart.assert_called_once_with(
                target_ms=4321, suppress_completion=True
            )

    def test_shared_shutdown_stops_external_apple_radio(self):
        with tempfile.TemporaryDirectory() as folder:
            player = play.Player(folder=folder)
            player.apple_radio_enabled = True
            player.apple_radio_active = "Apple Music 1"
            with (
                mock.patch.object(player, "_save_session_if_due"),
                mock.patch.object(player, "_stop_apple_radio_audio") as stop_radio,
                mock.patch.object(play.pygame, "mixer"),
            ):
                player._shutdown_audio()
                player._shutdown_audio()

            stop_radio.assert_called_once_with()
            self.assertFalse(player.apple_radio_enabled)
            self.assertEqual(player.apple_radio_active, "")

    def test_session_snapshot_records_local_playback_state(self):
        player = bare_player()
        player._cache = {"settings": {}}
        player.vol = 0.7
        player.play_mode = "shuffle"
        player.tab_names = ["All", "Mix"]
        player.active_tab = 1
        player._all_songs_set = {"song"}
        player.current = "song"
        player._play_start = 1
        with mock.patch.object(player, "_current_pos_in_song", return_value=(4321, 9000)):
            session = player._session_snapshot()

        self.assertEqual(
            session,
            {
                "volume": 0.7,
                "play_mode": "shuffle",
                "playlist": "Mix",
                "song": "song",
                "position_ms": 4321,
            },
        )

    def test_saved_session_restores_song_at_position_paused(self):
        player = bare_player()
        player._saved_session = {"song": "song", "position_ms": 4321}
        player._all_songs_set = {"song"}
        player.folder = "/music"
        player.current = "None"
        player.paused = False
        player._song_len_ms = 0
        player._current_playback_path = ""
        player._pause_offset = 0
        player._last_session_snapshot = None
        player._cache = {"settings": {}}
        player.vol = 0.4
        player.play_mode = "loop"
        player.tab_names = ["All"]
        player.active_tab = 0
        player._current_volume_multiplier = 1.0
        player._last_volume_apply_at = 0
        player._youtube_temp_path = None
        music = mock.Mock()
        mixer = mock.Mock()
        mixer.music = music
        with (
            mock.patch.object(play.pygame, "mixer", mixer),
            mock.patch.object(player, "_cached_duration_ms", return_value=9000),
            mock.patch.object(player, "_cached_volume_multiplier", return_value=0.8),
            mock.patch.object(player, "_apply_volume"),
        ):
            restored = player._restore_saved_session()

        self.assertTrue(restored)
        self.assertEqual(player.current, "song")
        self.assertTrue(player.paused)
        music.play.assert_called_once_with(loops=0, start=4.321)
        music.pause.assert_called_once_with()

    def test_refresh_library_reports_external_additions_and_removals(self):
        player = bare_player()
        player.all_songs = ["old", "kept"]
        player._all_songs_set = {"old", "kept"}
        player.current = "None"
        player._last_session_save_at = 0
        notifications = []

        def rebuild():
            player.all_songs = ["kept", "new"]
            player._all_songs_set = {"kept", "new"}

        with (
            mock.patch.object(player, "_rebuild", side_effect=rebuild),
            mock.patch.object(player, "_rebuild_play_pool"),
            mock.patch.object(player, "_emit_notification", side_effect=notifications.append),
            mock.patch.object(player, "_mark_all_dirty"),
            mock.patch.object(player, "_save_session_if_due"),
        ):
            changes = player._refresh_library()

        self.assertEqual(changes, (1, 1))
        self.assertEqual(player.status_msg, "Library refreshed: 1 added, 1 removed")
        self.assertEqual(notifications, [player.status_msg])

    def test_delete_moves_mp3_to_trash_and_updates_library_state(self):
        player = bare_player()
        with tempfile.TemporaryDirectory() as folder:
            player.folder = folder
            path = Path(folder, "song.mp3")
            path.touch()
            player.delete_confirm_songs = ["song"]
            player.current = "None"
            player._song_len_ms = 0
            player.paused = False
            player.playlists = {"Mix": ["song"]}
            player.selected_songs = {"song"}
            player.focused = "search"
            player._needs_redraw_hdr = False
            player._needs_redraw_bar = False
            player._needs_redraw_inp = False
            trashed = []

            def fake_trash(file_path):
                trashed.append(file_path)
                Path(file_path).unlink()

            with (
                mock.patch.object(player, "_move_to_trash", side_effect=fake_trash),
                mock.patch.object(player, "_save_playlists"),
                mock.patch.object(player, "_rebuild"),
                mock.patch.object(player, "_rebuild_play_pool"),
            ):
                player._confirm_delete_mp3()

            self.assertEqual(trashed, [str(path)])
            self.assertFalse(path.exists())
            self.assertEqual(player.playlists["Mix"], [])
            self.assertEqual(player.status_msg, "Moved 1 MP3 to Trash")

    def test_failed_trash_move_does_not_remove_playlist_entry(self):
        player = bare_player()
        player.folder = "/tmp"
        player.delete_confirm_songs = ["song"]
        player.current = "song"
        player._song_len_ms = 1000
        player.paused = False
        player.playlists = {"Mix": ["song"]}
        player.selected_songs = {"song"}
        player.focused = "search"
        player._needs_redraw_hdr = False
        player._needs_redraw_bar = False
        player._needs_redraw_inp = False
        with (
            mock.patch.object(player, "_move_to_trash", side_effect=OSError("no")),
            mock.patch.object(player, "_save_playlists"),
            mock.patch.object(player, "_rebuild"),
            mock.patch.object(player, "_rebuild_play_pool"),
        ):
            player._confirm_delete_mp3()
        self.assertEqual(player.playlists["Mix"], ["song"])
        self.assertEqual(player.current, "song")
        self.assertEqual(player.status_msg, "Moved 0 to Trash, failed 1")

    def test_constructor_scans_library_and_tolerates_legacy_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "Zulu.MP3").touch()
            Path(folder, "alpha.mp3").touch()
            Path(folder, "ignore.txt").touch()
            Path(folder, play.META_FILE).write_text(
                json.dumps({"alpha": {"plays": "3"}})
            )

            def fake_layout(player):
                player.win_lst = FakeListWindow(8)
                player._mark_all_dirty()

            fake_mixer = mock.Mock()
            with (
                mock.patch.object(play.pygame, "mixer", fake_mixer),
                mock.patch.object(play.Player, "_setup_curses"),
                mock.patch.object(play.Player, "_layout", fake_layout),
                mock.patch.object(play.Player, "_start_media_key_listener"),
                mock.patch.object(play.Player, "_start_screen_lock_watcher"),
                mock.patch.object(play.Player, "_start_duration_loader"),
                mock.patch.object(play.threading, "Thread") as thread,
            ):
                player = play.Player(mock.Mock(), folder)

            self.assertEqual(player.all_songs, ["alpha", "Zulu"])
            self.assertEqual(player.songs, ["alpha", "Zulu"])
            self.assertEqual(player._plays("alpha"), 3)
            thread.assert_called_once()

    def test_mp3_rename_persists_renamed_metadata(self):
        player = bare_player()
        with tempfile.TemporaryDirectory() as folder:
            player.folder = folder
            Path(folder, "old.mp3").touch()
            player.mp3_rename_old = "old"
            player.mp3_rename_buf = "new"
            player.mp3_rename_cursor = 3
            player.focused = "search"
            player.meta = {
                "old": {
                    "plays": 7,
                    "lyrics_cache": {
                        "text": "Lyrics for the old filename",
                        "source": "LRCLIB",
                    },
                }
            }
            player.playlists = {}
            player._plays_cache = {"old": 7}
            player._duration_ms_cache = {}
            player._total_listen_hours_cache = {}
            player._lyrics_memory_cache = {
                "old": {"text": "Old lyrics"},
                "new": {"text": "Earlier lyrics for new"},
            }
            player._lyrics_request_id = 4
            player.lyrics_song = "old"
            player.lyrics_status = ""
            player.lyrics_text = "Old lyrics"
            player.lyrics_source = "LRCLIB"
            player.lyrics_source_url = "https://lrclib.net"
            player._lyrics_loading = True
            player.current = "old"
            player.selected_songs = set()
            player._selection_anchor = None
            player._needs_redraw_hdr = False
            player._needs_redraw_bar = False
            player._needs_redraw_inp = False
            with (
                mock.patch.object(player, "_save_playlists"),
                mock.patch.object(player, "_rebuild"),
                mock.patch.object(player, "_rebuild_play_pool") as rebuild_pool,
            ):
                player._finish_mp3_rename()

            saved = json.loads(Path(folder, play.META_FILE).read_text())
            self.assertEqual(saved["songs"], {"new": {"plays": 7}})
            self.assertEqual(saved["playlists"], {})
            self.assertEqual(player.current, "new")
            self.assertEqual(player._lyrics_memory_cache, {})
            self.assertEqual(player._lyrics_request_id, 5)
            self.assertEqual(player.lyrics_song, "")
            self.assertEqual(player.lyrics_text, "")
            self.assertEqual(saved["analysis"], {})
            self.assertTrue(Path(folder, "new.mp3").exists())
            rebuild_pool.assert_called_once_with()


class ScrollingTests(unittest.TestCase):
    def test_scrollbar_thumb_can_be_dragged_to_bottom(self):
        player = bare_player(visible=10)
        player.songs = list(range(100))
        player.win_lst = mock.Mock()
        player.win_lst.getbegyx.return_value = (9, 20)
        player.win_lst.getmaxyx.return_value = (13, 80)
        player._scroll_dragging = False
        player._scroll_drag_offset = 0

        geometry = player._scrollbar_geometry()
        column, track_y, visible, _, _, maximum = geometry
        # The protected gutter is wider than the one-character track.
        self.assertTrue(player._start_scrollbar_drag(track_y, column - 1))
        self.assertTrue(player._scroll_dragging)
        player._drag_scrollbar_to(track_y + visible - 1)

        self.assertEqual(player.scroll, maximum)
        self.assertTrue(player._needs_redraw_lst)

    def test_clicking_scrollbar_track_jumps_without_playing_a_song(self):
        player = bare_player(visible=10)
        player.songs = list(range(100))
        player.win_lst = mock.Mock()
        player.win_lst.getbegyx.return_value = (5, 7)
        player.win_lst.getmaxyx.return_value = (13, 50)
        player._scroll_dragging = False
        player._scroll_drag_offset = 0

        column, track_y, visible, _, _, _ = player._scrollbar_geometry()
        handled = player._start_scrollbar_drag(
            track_y + visible // 2, column + 1
        )

        self.assertTrue(handled)
        self.assertGreater(player.scroll, 0)

    def test_scrollbar_gutter_is_exactly_three_columns_wide(self):
        player = bare_player(visible=10)
        player.win_lst = mock.Mock()
        player.win_lst.getbegyx.return_value = (5, 7)
        player.win_lst.getmaxyx.return_value = (13, 50)

        right_border = 7 + 50 - 1
        self.assertFalse(player._in_scrollbar_gutter(8, right_border - 4))
        self.assertTrue(player._in_scrollbar_gutter(8, right_border - 3))
        self.assertTrue(player._in_scrollbar_gutter(8, right_border - 2))
        self.assertTrue(player._in_scrollbar_gutter(8, right_border - 1))

    def test_context_menu_scroll_down_requests_a_redraw(self):
        player = bare_player()
        player.stdscr = FakeListWindow(8)
        player.ctx_menu_items = [("item", None, str(i)) for i in range(10)]
        player.ctx_menu_sel = 0
        player.ctx_menu_scroll = 0
        player._needs_redraw_ctx = False
        player._ctx_scroll_down()
        self.assertTrue(player.dirty)
        self.assertTrue(player._needs_redraw_ctx)

    def test_context_menu_navigation_skips_separators(self):
        player = bare_player()
        player.stdscr = FakeListWindow(12)
        player.ctx_menu_items = [
            ("item", None, "one"),
            ("separator", None, ""),
            ("item", None, "two"),
        ]
        player.ctx_menu_sel = 0
        player.ctx_menu_scroll = 0
        player._needs_redraw_ctx = False
        player._ctx_scroll_down()
        self.assertEqual(player.ctx_menu_sel, 2)
        player._ctx_scroll_up()
        self.assertEqual(player.ctx_menu_sel, 0)

    def test_scroll_boundaries_for_library_youtube_and_radio(self):
        player = bare_player(visible=4)
        player.songs = list(range(10))
        self.assertEqual(player._max_scroll(), 6)
        player._scroll_down(99)
        self.assertEqual(player.scroll, 6)
        player._scroll_up(99)
        self.assertEqual(player.scroll, 0)

        player.youtube_preview_enabled = True
        player.youtube_results = list(range(8))
        self.assertEqual(player._max_scroll(), 4)

        player.youtube_preview_enabled = False
        player.apple_radio_enabled = True
        self.assertEqual(player._max_scroll(), 0)

    def test_negative_scroll_amount_cannot_move_or_escape_bounds(self):
        player = bare_player(visible=3)
        player.songs = list(range(10))
        player.scroll = 4
        player._scroll_up(-5)
        self.assertEqual(player.scroll, 4)
        player._scroll_down(-5)
        self.assertEqual(player.scroll, 4)

    def test_keyboard_scroll_keys_cover_lines_pages_home_and_end(self):
        player = bare_player(visible=5)
        player.songs = list(range(20))
        self.assertTrue(player._handle_list_scroll_key(play.curses.KEY_DOWN))
        self.assertEqual(player.scroll, 1)
        self.assertTrue(player._handle_list_scroll_key(play.curses.KEY_NPAGE))
        self.assertEqual(player.scroll, 5)
        self.assertTrue(player._handle_list_scroll_key(play.curses.KEY_END))
        self.assertEqual(player.scroll, 15)
        self.assertTrue(player._handle_list_scroll_key(play.curses.KEY_PPAGE))
        self.assertEqual(player.scroll, 11)
        self.assertTrue(player._handle_list_scroll_key(play.curses.KEY_HOME))
        self.assertEqual(player.scroll, 0)
        self.assertFalse(player._handle_list_scroll_key(ord("x")))

    def test_clamp_scroll_handles_negative_and_shrinking_lists(self):
        player = bare_player(visible=4)
        player.songs = list(range(10))
        player.scroll = -3
        player._clamp_scroll()
        self.assertEqual(player.scroll, 0)
        player.scroll = 6
        player.songs = list(range(5))
        player._clamp_scroll()
        self.assertEqual(player.scroll, 1)

    def test_youtube_rebuild_preserves_valid_scroll_position(self):
        player = bare_player(visible=4)
        player.youtube_preview_enabled = True
        player.youtube_results = list(range(10))
        player.scroll = 5
        player.selected_songs = {"old"}
        player._selection_anchor = "old"
        player.col_width = 8
        player._schedule_youtube_result_search = lambda: False
        player._needs_redraw_hdr = False

        player._apply_search_filter()

        self.assertEqual(player.scroll, 5)
        self.assertEqual(player.songs, [])
        self.assertEqual(player.selected_songs, set())

    def test_local_filter_clamps_scroll_after_results_shrink(self):
        player = bare_player(visible=3)
        player.all_songs = ["alpha.mp3", "beta.mp3", "gamma.mp3", "zeta.mp3"]
        player._all_songs_set = set(player.all_songs)
        player._song_lower_cache = {name: name.lower() for name in player.all_songs}
        player.playlists = {}
        player.active_tab = 0
        player.tab_names = ["All"]
        player.search = "zeta"
        player.sort_mode = "name"
        player.sort_reverse = False
        player._plays_cache = {}
        player.selected_songs = set()
        player._selection_anchor = None
        player.scroll = 3
        player._needs_redraw_hdr = False

        player._apply_search_filter()

        self.assertEqual(player.songs, ["zeta.mp3"])
        self.assertEqual(player.scroll, 0)

    def test_radio_playback_does_not_empty_the_visible_mp3_library(self):
        player = bare_player(visible=3)
        player.apple_radio_enabled = True
        player.all_songs = ["alpha", "beta"]
        player._all_songs_set = set(player.all_songs)
        player._song_lower_cache = {name: name for name in player.all_songs}
        player.playlists = {}
        player.active_tab = 0
        player.tab_names = ["All"]
        player.search = ""
        player.sort_mode = "name"
        player.sort_reverse = False
        player._plays_cache = {}
        player.selected_songs = set()
        player._selection_anchor = None
        player._needs_redraw_hdr = False

        player._apply_search_filter()

        self.assertEqual(player.songs, ["alpha", "beta"])

    def test_viewed_playlist_becomes_the_playback_and_shuffle_source(self):
        player = bare_player()
        player.all_songs = ["alpha", "beta", "gamma"]
        player._all_songs_set = set(player.all_songs)
        player._song_lower_cache = {name: name for name in player.all_songs}
        player.playlists = {"Mix": ["beta", "gamma"]}
        player.playlist_tags = {}
        player.tab_names = ["All", "Mix"]
        player.active_tab = 1
        player.play_tab = "All"
        player.play_pool = list(player.all_songs)
        player._pending_shuffle_next = "alpha"
        player.sort_mode = "name"
        player.sort_reverse = False
        player._plays_cache = {}
        player._needs_redraw_hdr = False

        source = player._use_viewed_playlist_for_playback()

        self.assertEqual(source, "Mix")
        self.assertEqual(player.play_tab, "Mix")
        self.assertEqual(player.play_pool, ["beta", "gamma"])
        self.assertIsNone(player._pending_shuffle_next)


class PureHelperTests(unittest.TestCase):
    def test_mp3_delete_uses_centered_confirmation_and_cleans_up_on_cancel(self):
        player = bare_player()
        player.delete_confirm_songs = []
        player.confirm_open = False
        player.confirm_msg = ""
        player.confirm_action = None
        player.confirm_cancel_action = None
        player.confirm_sel = 1
        player._confirm_btn_regions = []
        player._needs_redraw_confirm = False
        player._clear_selection_after_action = mock.Mock()
        player._mark_all_dirty = mock.Mock()
        player.focused = "list"
        player.status_msg = ""
        player._needs_redraw_hdr = False
        player._needs_redraw_inp = False

        player._start_delete_confirm(["song"])

        self.assertTrue(player.confirm_open)
        self.assertEqual(player.confirm_msg, "Delete MP3 'song'?")
        player._confirm_no()
        self.assertFalse(player.confirm_open)
        self.assertEqual(player.delete_confirm_songs, [])
        self.assertEqual(player.status_msg, "Delete canceled")

    def test_duration_cache_uses_matching_file_mtime(self):
        player = bare_player()
        with tempfile.TemporaryDirectory() as folder:
            player.folder = folder
            path = Path(folder, "song.mp3")
            path.touch()
            mtime = path.stat().st_mtime_ns
            player.meta = {
                "song": {"duration_ms": 1234, "duration_mtime_ns": mtime}
            }
            player._duration_ms_cache = {}
            player._queue_duration_load = mock.Mock()
            self.assertEqual(player._cached_duration_ms("song"), 1234)
            player._queue_duration_load.assert_not_called()

    def test_duration_queue_wakes_sleeping_loader(self):
        player = bare_player()
        player._duration_ms_cache = {}
        player._duration_load_pending = set()
        player._duration_load_queue = play.deque()
        player._duration_load_lock = play.threading.Lock()
        player._duration_load_wakeup = play.threading.Event()

        player._queue_duration_load("song")

        self.assertTrue(player._duration_load_wakeup.is_set())
        self.assertEqual(list(player._duration_load_queue), ["song"])

    def test_energy_saving_poll_rates_avoid_rapid_process_churn(self):
        self.assertGreaterEqual(play.OUTPUT_SAFETY_ACTIVE_POLL_S, 1.0)
        self.assertGreaterEqual(play.OUTPUT_SAFETY_IDLE_POLL_S, 5.0)
        self.assertEqual(play.TEXTUAL_TICK_S, 1.0)

    def test_output_watcher_spawns_nothing_while_safety_is_disabled(self):
        player = bare_player()
        player.running = True
        player._shutting_down = False
        player.non_speaker_mode = False
        player._output_safety_wakeup = mock.Mock()
        player._output_safety_wakeup.wait.side_effect = lambda **_: setattr(
            player, "running", False
        )
        thread = mock.Mock()
        with (
            mock.patch.object(play.sys, "platform", "darwin"),
            mock.patch.object(play.shutil, "which", return_value="/bin/SwitchAudioSource"),
            mock.patch.object(play.threading, "Thread", return_value=thread) as make_thread,
            mock.patch.object(play.subprocess, "run") as run,
        ):
            player._start_output_safety_watcher()
            make_thread.call_args.kwargs["target"]()

        run.assert_not_called()

    def test_output_watcher_uses_energy_saving_active_interval(self):
        player = bare_player()
        player.running = True
        player._shutting_down = False
        player.non_speaker_mode = True
        player.current = "song"
        player.paused = False
        player._handle_output_device = mock.Mock()
        player._output_safety_wakeup = mock.Mock()
        player._output_safety_wakeup.wait.side_effect = lambda **_: setattr(
            player, "running", False
        )
        result = mock.Mock(returncode=0, stdout="Headphones\n")
        thread = mock.Mock()
        with (
            mock.patch.object(play.sys, "platform", "darwin"),
            mock.patch.object(play.shutil, "which", return_value="/bin/SwitchAudioSource"),
            mock.patch.object(play.threading, "Thread", return_value=thread) as make_thread,
            mock.patch.object(play.subprocess, "run", return_value=result) as run,
        ):
            player._start_output_safety_watcher()
            make_thread.call_args.kwargs["target"]()

        run.assert_called_once()
        player._handle_output_device.assert_called_once_with("Headphones\n")
        player._output_safety_wakeup.wait.assert_called_once_with(
            timeout=play.OUTPUT_SAFETY_ACTIVE_POLL_S
        )

    def test_volume_adjustment_clamps_and_applies_once(self):
        player = bare_player()
        player.vol = 0.9
        player._needs_redraw_hdr = False
        player._apply_volume = mock.Mock()
        player._adjust_volume(5)
        self.assertEqual(player.vol, 1.0)
        player._apply_volume.assert_called_once_with(force=True)
        player._adjust_volume(1)
        player._apply_volume.assert_called_once_with(force=True)
        player._adjust_volume(-20)
        self.assertEqual(player.vol, 0.0)

    def test_elapsed_time_accounts_for_pause_duration(self):
        player = bare_player()
        player._play_start = 100.0
        player._pause_offset = 2.0
        player.paused = False
        with mock.patch.object(play.time, "monotonic", return_value=107.0):
            self.assertEqual(player._elapsed_ms(), 5_000)
        player.paused = True
        player._pause_start = 106.0
        self.assertEqual(player._elapsed_ms(), 4_000)

    def test_screen_lock_only_queues_pause_and_matching_resume(self):
        player = bare_player()
        player._screen_locked = False
        player._paused_by_lock = False
        player._pending_media_toggle = False
        player.current = "song"
        player.paused = False
        player._handle_lock_state(True)
        self.assertTrue(player._screen_locked)
        self.assertTrue(player._paused_by_lock)
        self.assertTrue(player._pending_media_toggle)

        player._pending_media_toggle = False
        player.paused = True
        player._handle_lock_state(False)
        self.assertFalse(player._screen_locked)
        self.assertTrue(player._pending_media_toggle)

    def test_selection_shift_range_and_clear(self):
        player = bare_player()
        player.songs = ["a", "b", "c", "d"]
        player.selected_songs = {"b"}
        player._selection_anchor = "b"
        player._select_song_number("d", 3, shift=True)
        self.assertEqual(player.selected_songs, {"b", "c", "d"})
        self.assertTrue(player._clear_selection())
        self.assertEqual(player.selected_songs, set())

    def test_download_file_discovery_prefers_audio_formats(self):
        player = bare_player()
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "track.webm").touch()
            Path(folder, "track.mp3").touch()
            Path(folder, "ignored.mp3.part").touch()
            self.assertTrue(player._find_downloaded_media(folder).endswith("track.mp3"))
            self.assertTrue(player._find_downloaded_ext(folder, "webm").endswith("track.webm"))
            self.assertEqual(player._unique_path(str(Path(folder, "new.mp3"))), str(Path(folder, "new.mp3")))
    def test_volume_analysis_does_not_hold_queue_lock_during_expensive_work(self):
        class TrackingLock:
            def __init__(self):
                self.inside = False

            def __enter__(self):
                self.inside = True

            def __exit__(self, *_):
                self.inside = False

        class ImmediateThread:
            def __init__(self, target, **_):
                self.target = target

            def start(self):
                self.target()

        player = bare_player()
        player._all_songs_set = {"song"}
        player._volume_multiplier_pending = set()
        player._volume_multiplier_lock = TrackingLock()
        player._volume_analysis_lock = TrackingLock()
        player._stored_volume_multiplier = mock.Mock(return_value=None)
        player.current = "None"
        player._needs_redraw_hdr = False
        compute_queue_lock_states = []

        def compute(_name):
            compute_queue_lock_states.append(player._volume_multiplier_lock.inside)
            return 1.0

        player._compute_volume_multiplier = compute
        with (
            mock.patch.object(play.shutil, "which", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(play.threading, "Thread", ImmediateThread),
        ):
            player._queue_volume_multiplier_compute("song")
        self.assertEqual(compute_queue_lock_states, [False])
        self.assertEqual(player._volume_multiplier_pending, set())

    def test_finished_shuffle_track_wakes_ui_for_immediate_handoff(self):
        player = bare_player()
        player.running = True
        player._shutting_down = False
        player.current = "finished"
        player.paused = False
        player._youtube_temp_path = None
        player._song_len_ms = 1000
        player._play_start = 1
        player.play_mode = "shuffle"
        player._playback_id = 1
        player._completion_suppressed_until = 0
        player._pending_shuffle_next = None
        player.play_pool = ["next"]
        player._queue_play_inc = mock.Mock()
        player._weighted_shuffle_choice = mock.Mock(return_value="next")
        player._wake_ui = mock.Mock(
            side_effect=lambda: setattr(player, "running", False)
        )

        with (
            mock.patch.object(play.time, "sleep"),
            mock.patch.object(play.time, "monotonic", return_value=10),
            mock.patch.object(play.pygame.mixer.music, "get_busy", return_value=False),
        ):
            player._watch_playback()

        self.assertEqual(player._pending_shuffle_next, "next")
        player._wake_ui.assert_called_once_with()

    def test_finished_track_with_unknown_duration_still_advances(self):
        player = bare_player()
        player.running = True
        player._shutting_down = False
        player.current = "finished"
        player.paused = False
        player._youtube_temp_path = None
        player._song_len_ms = 0
        player._play_start = 1
        player.play_mode = "shuffle"
        player._playback_id = 1
        player._completion_suppressed_until = 0
        player._pending_shuffle_next = None
        player.play_pool = ["next"]
        player._queue_play_inc = mock.Mock()
        player._weighted_shuffle_choice = mock.Mock(return_value="next")
        player._wake_ui = mock.Mock(
            side_effect=lambda: setattr(player, "running", False)
        )

        with (
            mock.patch.object(play.time, "sleep"),
            mock.patch.object(play.time, "monotonic", return_value=10),
            mock.patch.object(play.pygame.mixer.music, "get_busy", return_value=False),
        ):
            player._watch_playback()

        self.assertEqual(player._pending_shuffle_next, "next")

    def test_text_editor_navigation_deletion_and_kill_yank(self):
        player = bare_player()
        player.search = "one two"
        player.search_cursor = len(player.search)
        player._text_kill_ring = ""
        player._needs_redraw_inp = False
        player._search_changed = mock.Mock()

        self.assertTrue(player._edit_text_field("search", 23))  # Ctrl-W
        self.assertEqual(player.search, "one ")
        self.assertEqual(player._text_kill_ring, "two")
        self.assertTrue(player._edit_text_field("search", 25))  # Ctrl-Y
        self.assertEqual(player.search, "one two")
        self.assertTrue(player._edit_text_field("search", play.curses.KEY_HOME))
        self.assertEqual(player.search_cursor, 0)
        self.assertTrue(player._edit_text_field("search", play.curses.KEY_DC))
        self.assertEqual(player.search, "ne two")

    def test_playlist_rename_editor_redraws_shared_input_bar(self):
        player = bare_player()
        player.rename_buf = "Mix"
        player.rename_cursor = 3
        player._text_kill_ring = ""
        player._needs_redraw_inp = False
        player._needs_redraw_tabs = False

        self.assertTrue(player._edit_text_field("tab_rename", ord("!")))

        self.assertEqual(player.rename_buf, "Mix!")
        self.assertEqual(player.rename_cursor, 4)
        self.assertTrue(player._needs_redraw_inp)
        self.assertFalse(player._needs_redraw_tabs)

    def test_playlist_round_trip_deduplicates_invalid_entries(self):
        player = bare_player()
        with tempfile.TemporaryDirectory() as folder:
            player.folder = folder
            Path(folder, play.PLAYLISTS_FILE).write_text(
                json.dumps(
                    {
                        "All": ["ignored"],
                        "": ["ignored"],
                        "  Mix  ": ["a", "a", 2, "b"],
                    }
                )
            )
            self.assertEqual(player._load_playlists(), {"Mix": ["a", "b"]})

    def test_tagged_playlist_finds_matching_mp3s_and_manual_playlist_uses_drags(self):
        player = bare_player()
        player.all_songs = [
            "[OR] vampire", "[21P] Lavish", "[or] drivers license", "Golden"
        ]
        player.playlists = {"Olivia": ["Golden"]}
        player.playlist_tags = {"Olivia": "OR"}

        self.assertEqual(
            player._playlist_song_names("Olivia"),
            ["[OR] vampire", "[or] drivers license"],
        )
        player._emit_notification = mock.Mock()
        self.assertEqual(player._add_songs_to_playlist("Olivia", ["Golden"]), 0)
        self.assertEqual(player.playlists["Olivia"], ["Golden"])

        player.playlist_tags.clear()
        self.assertEqual(player._playlist_song_names("Olivia"), ["Golden"])

    def test_playlist_tag_rejects_square_brackets(self):
        player = bare_player()
        player.playlists = {"Olivia": []}
        player.playlist_tags = {}
        player._emit_notification = mock.Mock()

        self.assertFalse(player._set_playlist_tag("Olivia", "[OR]"))
        self.assertEqual(player.playlist_tags, {})
        player._emit_notification.assert_called_once()

    def test_legacy_json_files_migrate_to_one_cache(self):
        player = bare_player()
        with tempfile.TemporaryDirectory() as folder:
            player.folder = folder
            Path(folder, play.LEGACY_META_FILE).write_text(
                json.dumps({"song": {"plays": 3}})
            )
            Path(folder, play.LEGACY_PLAYLISTS_FILE).write_text(
                json.dumps({"Mix": ["song"]})
            )
            Path(folder, play.LEGACY_ANALYSIS_FILE).write_text(
                json.dumps({"_version": 5, "song": {"loudness": -10.0}})
            )

            cache = player._load_cache()

            self.assertEqual(cache["songs"], {"song": {"plays": 3}})
            self.assertEqual(cache["playlists"], {"Mix": ["song"]})
            self.assertEqual(
                cache["analysis"],
                {"_version": 5, "song": {"loudness": -10.0}},
            )
            self.assertTrue(Path(folder, play.CACHE_FILE).exists())
            self.assertFalse(Path(folder, play.LEGACY_META_FILE).exists())
            self.assertFalse(Path(folder, play.LEGACY_PLAYLISTS_FILE).exists())
            self.assertFalse(Path(folder, play.LEGACY_ANALYSIS_FILE).exists())

    def test_malformed_meta_shape_and_counts_are_safe(self):
        player = bare_player()
        with tempfile.TemporaryDirectory() as folder:
            player.folder = folder
            Path(folder, play.META_FILE).write_text(json.dumps(["not", "a", "dict"]))
            self.assertEqual(player._load_meta(), {})
        self.assertEqual(player._extract_count({"plays": "12"}), 12)
        self.assertEqual(player._extract_count({"plays": "broken"}), 0)
        self.assertEqual(player._extract_count({"plays": -2}), 0)

    def test_increment_repairs_string_play_count(self):
        player = bare_player()
        player.meta = {"song": {"plays": "4"}}
        player._plays_cache = {"song": 4}
        player._total_listen_hours_cache = {}
        player._meta_dirty = False
        player._inc_plays("song", 1)
        self.assertEqual(player.meta["song"]["plays"], 5)
        self.assertEqual(player._plays_cache["song"], 5)

    def test_playlist_rename_does_not_overwrite_existing_playlist(self):
        player = bare_player()
        player.playlists = {"Road": ["one"], "Work": ["two"]}
        player.status_msg = ""
        player._needs_redraw_hdr = False

        player._rename_playlist("Road", "Work")

        self.assertEqual(player.playlists, {"Road": ["one"], "Work": ["two"]})
        self.assertIn("already exists", player.status_msg)

    def test_volume_multiplier_peak_target_and_limits(self):
        self.assertAlmostEqual(play.Player._volume_multiplier_from_peak(-3), 1.0)
        self.assertAlmostEqual(play.Player._volume_multiplier_from_peak(-9), 1.0)
        self.assertLess(play.Player._volume_multiplier_from_peak(0), 1.0)
        self.assertEqual(play.Player._volume_multiplier_from_peak(None), 1.0)

    def test_youtube_count_parsing_and_formatting(self):
        player = bare_player()
        self.assertEqual(player._parse_yt_count("1.2K views"), 1200)
        self.assertEqual(player._parse_yt_count("3M"), 3_000_000)
        self.assertEqual(player._fmt_yt_count(1_250), "1.2K")
        self.assertEqual(player._fmt_yt_count(None), "-")
        self.assertEqual(player._fmt_yt_count(float("inf")), "-")
        self.assertIsNone(player._coerce_yt_int(float("inf")))

    def test_youtube_download_progress_parsing(self):
        progress = play.Player._parse_ytdlp_progress_line(
            "__ANTARCTITEEX_PROGRESS__ 42.5%\t3.2MiB/s\t00:08"
        )
        self.assertEqual(
            progress,
            {"percent": 42.5, "speed": "3.2MiB/s", "eta": "00:08"},
        )
        self.assertIsNone(
            play.Player._parse_ytdlp_progress_line("[download] ordinary output")
        )

    def test_bulk_playlist_add_reports_count_and_notification(self):
        player = bare_player()
        player.playlists = {"Mix": ["a"]}
        player.play_tab = "All"
        player._save_playlists = mock.Mock()
        player._rebuild = mock.Mock()
        notifications = []
        player._ui_notify = lambda message, **options: notifications.append(
            (message, options)
        )

        added = player._add_songs_to_playlist("Mix", ["a", "b", "c"])

        self.assertEqual(added, 2)
        self.assertEqual(player.playlists["Mix"], ["a", "b", "c"])
        self.assertEqual(notifications[0][0], "Added 2 songs to 'Mix'")

    def test_youtube_sort_tolerates_mixed_external_value_types(self):
        player = bare_player()
        player.youtube_sort_mode = "views"
        player.youtube_sort_reverse = True
        player.youtube_results = [
            {"title": "text", "views": "20", "kind": "video"},
            {"title": None, "views": 5, "kind": "video"},
            {"title": "bad", "views": "unknown", "kind": "video"},
        ]
        player._sort_youtube_results()
        self.assertEqual(player.youtube_results[0]["title"], "text")

    def test_youtube_urls_and_ids(self):
        player = bare_player()
        self.assertIn("search_query=a+%26+b", player._youtube_search_url("a & b"))
        self.assertEqual(
            player._youtube_video_id_from_url("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(player._youtube_abs_url("/@music"), "https://www.youtube.com/@music")
        self.assertEqual(
            player._youtube_channel_videos_target("https://youtube.com/@music"),
            "https://youtube.com/@music/videos",
        )

    def test_youtube_result_extraction_deduplicates_and_skips_invalid_rows(self):
        player = bare_player()
        entries = [
            {"id": "abc", "title": "First", "url": "abc", "view_count": 10},
            {"id": "abc", "title": "Duplicate", "url": "abc"},
            {"id": "", "title": ""},
            {"id": "UC123", "title": "Channel", "url": "@channel", "_type": "channel"},
        ]
        results = player._extract_youtube_search_results(entries, 10)
        self.assertEqual([row["kind"] for row in results], ["video", "channel"])
        self.assertEqual(results[0]["url"], "https://www.youtube.com/watch?v=abc")

    def test_ytdlp_format_fallback_removes_format_and_value(self):
        player = bare_player()
        self.assertEqual(
            player._without_ytdlp_format(["yt-dlp", "-f", "ba", "--quiet", "url"]),
            ["yt-dlp", "--quiet", "url"],
        )

    def test_time_and_truncation_formatters(self):
        self.assertEqual(play.Player._fmt_time(65_000), "1:05")
        self.assertEqual(play.Player._fmt_yt_duration(3_661), "1:01:01")
        self.assertEqual(play.Player._fmt_yt_duration(None), "--:--")
        self.assertEqual(play.Player._fmt_listen_hours(-2), "0.00h")
        self.assertEqual(play.Player._truncate("abcdef", 5), "ab...")
        self.assertEqual(
            play.Player._truncate_song_name("[Aurora] Runaway", 8),
            "[Aurora]",
        )
        self.assertEqual(
            play.Player._truncate_song_name("[21P] At The Risk", 12),
            "[21P] At...",
        )
        self.assertEqual(
            play.Player._without_artist_tag("[21P] At The Risk"),
            "At The Risk",
        )
        self.assertEqual(
            play.Player._without_artist_tag("Golden"),
            "Golden",
        )

    def test_filename_and_text_helpers(self):
        player = bare_player()
        self.assertEqual(player._clean_mp3_basename("  bad/name?.mp3  "), "bad name")
        self.assertEqual(player._clean_paste_text("a\r\nb\x00"), "a b")
        self.assertEqual(player._prev_word_pos("one two", 7), 4)
        self.assertEqual(player._next_word_pos("one two", 0), 4)

    def test_shuffle_weights_are_normalized(self):
        player = bare_player()
        player._plays_cache = {"a": 0, "b": 2, "c": 8}
        player.current = "None"
        rows = player._shuffle_weight_rows(["a", "b", "c"])
        self.assertAlmostEqual(sum(row["probability"] for row in rows), 1.0)
        weights = {row["name"]: row["weight"] for row in rows}
        self.assertGreater(weights["a"], weights["c"])


class LyricsTests(unittest.TestCase):
    def test_lyrics_filter_censors_swears_but_preserves_hell(self):
        player = play.Player.__new__(play.Player)
        player._lyrics_request_id = 1
        player.lyrics_song = "Example"
        player._lyrics_loading = True
        player._lyrics_memory_cache = {}
        player.meta = {}
        player._wake_ui = lambda: None

        player._finish_lyrics_request(
            1, "Example", {"text": "[Verse 1]\nHell is a place. Damn, that's shit.",
                           "source": "Genius"}
        )

        self.assertEqual(
            player.lyrics_text, "[Verse 1]\nHell is a place. ****, that's ****."
        )
        self.assertEqual(player.meta["Example"]["lyrics_cache"]["text"], player.lyrics_text)

    def test_lyrics_filter_censors_disguised_words_without_changing_safe_text(self):
        self.assertEqual(
            play._censor_lyrics_text(
                "s*x, s✱x, s•x, s-x, s*xy, s✱xy, sexy, sexting, "
                "sh✱t, b!tch; m*th*rf**ker, f*ckin', fuckin'; "
                "hell, shell, God, god, co-op."
            ),
            "****, ****, ****, ****, ****, ****, ****, ****, "
            "****, ****; ****, ****, ****; "
            "hell, shell, God, god, co-op.",
        )

    def test_existing_cached_lyrics_are_censored_when_loaded(self):
        player = play.Player.__new__(play.Player)
        player._all_songs_set = {"Example"}
        player._lyrics_request_id = 0
        player.lyrics_song = ""
        player._lyrics_loading = False
        player.lyrics_text = ""
        player._lyrics_memory_cache = {}
        player.meta = {"Example": {"lyrics_cache": {
            "text": "What the hell? This is shit, s✱x and s*xy.",
            "source": "LRCLIB",
            "cache_version": play.LYRICS_CACHE_VERSION,
        }}}
        player._wake_ui = lambda: None

        player.request_lyrics("Example")

        self.assertEqual(player.lyrics_text, "What the hell? This is ****, **** and ****.")
        self.assertEqual(player.meta["Example"]["lyrics_cache"]["text"], player.lyrics_text)

    def test_playlist_artist_name_expands_tags_without_hardcoded_aliases(self):
        player = play.Player.__new__(play.Player)
        player.playlist_tags = {
            "Olivia Rodrigo": "OR",
            "Twenty One Pilots": "21P",
            "My Favorites": "MF",
        }
        self.assertEqual(player._playlist_artist_for_tag("OR"), "Olivia Rodrigo")
        self.assertEqual(player._playlist_artist_for_tag("21P"), "Twenty One Pilots")
        self.assertEqual(player._playlist_artist_for_tag("AG"), "")
        self.assertEqual(player._playlist_artist_for_tag("X"), "")

    def test_numbered_artist_tags_match_both_digit_and_word_initials(self):
        matches = play.Player._artist_matches_tag
        self.assertTrue(matches("Twenty One Pilots", "21P"))
        self.assertTrue(matches("Twenty-One Pilots", "TOP"))
        self.assertTrue(matches("21 Pilots", "TOP"))
        self.assertTrue(matches("One Direction", "1D"))
        self.assertFalse(matches("Twenty One Savage", "21P"))
        self.assertFalse(matches("Two One Pilots", "21P"))
        self.assertFalse(matches("Wrong Artist", "OR"))
        self.assertEqual(
            play.Player._artist_tag_query_variants("21P"),
            ["21P", "twenty one P", "TOP"],
        )
        self.assertEqual(
            play.Player._artist_tag_query_variants("7R"),
            ["7R", "seven R", "SR"],
        )

    def test_genius_tries_number_word_initials_and_rejects_wrong_artist(self):
        player = play.Player.__new__(play.Player)
        wrong = {"response": {"hits": [{"result": {
            "title": "Stressed Out", "url": "https://genius.com/wrong",
            "primary_artist": {"name": "Twenty One Savage"},
        }}]}}
        right = {"response": {"hits": [{"result": {
            "title": "Stressed Out", "url": "https://genius.com/right",
            "primary_artist": {"name": "Twenty One Pilots"},
        }}]}}
        page = '<div data-lyrics-container="true">[Verse 1]<br>Right</div>'
        with (
            mock.patch.object(player, "_search_genius", side_effect=[wrong, right]) as search,
            mock.patch.object(player, "_fetch_text", return_value=page) as fetch_page,
        ):
            result = player._fetch_genius_lyrics({
                "artist": "21P", "artist_tag": "21P", "title": "Stressed Out",
            })
        self.assertEqual([call.args[0] for call in search.call_args_list],
                         ["Stressed Out 21P", "Stressed Out twenty one P"])
        fetch_page.assert_called_once_with("https://genius.com/right")
        self.assertEqual(result["text"], "[Verse 1]\nRight")

    def test_genius_tries_initials_after_spelled_number_query(self):
        player = play.Player.__new__(play.Player)
        empty = {"response": {"hits": []}}
        right = {"response": {"hits": [{"result": {
            "title": "Stressed Out", "url": "https://genius.com/right",
            "primary_artist": {"name": "Twenty One Pilots"},
        }}]}}
        page = '<div data-lyrics-container="true">[Verse 1]<br>Right</div>'
        with (
            mock.patch.object(player, "_search_genius", side_effect=[empty, empty, right]) as search,
            mock.patch.object(player, "_fetch_text", return_value=page),
        ):
            result = player._fetch_genius_lyrics({
                "artist": "21P", "artist_tag": "21P", "title": "Stressed Out",
            })
        self.assertEqual(
            [call.args[0] for call in search.call_args_list],
            ["Stressed Out 21P", "Stressed Out twenty one P", "Stressed Out TOP"],
        )
        self.assertEqual(result["url"], "https://genius.com/right")

    def test_genius_rejects_playlist_hint_with_mismatched_initials(self):
        player = play.Player.__new__(play.Player)
        search = {"response": {"hits": [{"result": {
            "title": "vampire", "url": "https://genius.com/wrong",
            "primary_artist": {"name": "Wrong Artist"},
        }}]}}
        with (
            mock.patch.object(player, "_search_genius", return_value=search),
            mock.patch.object(player, "_fetch_text") as fetch_page,
        ):
            result = player._fetch_genius_lyrics({
                "artist": "OR", "artist_tag": "OR",
                "artist_hint": "Wrong Artist", "title": "vampire",
            })
        self.assertIsNone(result)
        fetch_page.assert_not_called()

    def test_ag_freak_infers_artist_from_clearer_sibling_song(self):
        player = play.Player.__new__(play.Player)
        player.folder = "/unused"
        player.playlist_tags = {}
        player.artist_hints = {}
        player.all_songs = ["[AG] freak", "[AG] 7 rings"]
        sibling_search = {"response": {"hits": [{"result": {
            "title": "7 rings",
            "url": "https://genius.com/Ariana-grande-7-rings-lyrics",
            "primary_artist": {"name": "Ariana Grande"},
        }}]}}
        freak_search = {"response": {"hits": [{"result": {
            "title": "freak",
            "url": "https://genius.com/Ariana-grande-freak-lyrics",
            "primary_artist": {"name": "Ariana Grande"},
        }}]}}
        page = '<div data-lyrics-container="true">[Verse 1]<br>Words</div>'
        with (
            mock.patch.object(play.shutil, "which", return_value=None),
            mock.patch.dict(play.os.environ, {"GENIUS_ACCESS_TOKEN": ""}),
            mock.patch.object(
                player, "_fetch_json", side_effect=[sibling_search, freak_search]
            ) as fetch,
            mock.patch.object(player, "_fetch_text", return_value=page),
        ):
            metadata = player._read_audio_lyrics_metadata("[AG] freak")
            metadata["library_name"] = "[AG] freak"
            result = player._fetch_genius_lyrics(metadata)
        self.assertEqual(metadata["artist_hint"], "Ariana Grande")
        self.assertIn("7+rings", fetch.call_args_list[0].args[0])
        self.assertIn("freak+Ariana+Grande", fetch.call_args.args[0])
        self.assertEqual(result["url"], "https://genius.com/Ariana-grande-freak-lyrics")
        self.assertEqual(player.artist_hints["AG"], "Ariana Grande")
        self.assertEqual(player._playlist_artist_for_tag("AG"), "Ariana Grande")

    def test_tag_inference_rejects_unrelated_artist(self):
        player = play.Player.__new__(play.Player)
        player.folder = "/unused"
        player.playlist_tags = {}
        player.artist_hints = {}
        player.all_songs = ["[AG] freak", "[AG] 7 rings"]
        search = {"response": {"hits": [{"result": {
            "title": "7 rings", "primary_artist": {"name": "Wrong Artist"},
        }}]}}
        with (
            mock.patch.object(play.shutil, "which", return_value=None),
            mock.patch.object(player, "_search_genius", return_value=search),
        ):
            self.assertEqual(player._infer_artist_for_tag("AG", "[AG] freak"), "")
        self.assertEqual(player.artist_hints, {})

    def test_inferred_artist_is_saved_in_unified_cache(self):
        player = play.Player.__new__(play.Player)
        player._cache = play.Player._empty_cache()
        player.artist_hints = {"AG": "Ariana Grande"}
        player._session_snapshot = lambda: {}
        with mock.patch.object(player, "_write_cache", return_value=True) as write:
            player._save_cache()
        self.assertEqual(
            write.call_args.args[0]["settings"]["artist_hints"],
            {"AG": "Ariana Grande"},
        )

    def test_filename_lyrics_metadata_includes_playlist_artist_hint(self):
        player = play.Player.__new__(play.Player)
        player.folder = "/unused"
        player.playlist_tags = {"Twenty One Pilots": "21P"}
        with mock.patch.object(play.shutil, "which", return_value=None):
            metadata = player._read_audio_lyrics_metadata("[21P] Stressed Out")
        self.assertEqual(metadata["artist"], "21P")
        self.assertEqual(metadata["artist_hint"], "Twenty One Pilots")

    def test_playlist_artist_hint_is_used_for_lyrics_query(self):
        player = play.Player.__new__(play.Player)
        search = {"response": {"hits": [{"result": {
            "title": "Stressed Out",
            "url": "https://genius.com/Twenty-one-pilots-stressed-out-lyrics",
            "primary_artist": {"name": "Twenty One Pilots"},
        }}]}}
        page = '<div data-lyrics-container="true">[Verse 1]<br>Words</div>'
        with (
            mock.patch.dict(play.os.environ, {"GENIUS_ACCESS_TOKEN": ""}),
            mock.patch.object(player, "_fetch_json", return_value=search) as fetch,
            mock.patch.object(player, "_fetch_text", return_value=page),
        ):
            result = player._fetch_genius_lyrics({
                "artist": "21P", "artist_tag": "21P",
                "artist_hint": "Twenty One Pilots", "title": "Stressed Out",
            })
        self.assertIn("Twenty+One+Pilots", fetch.call_args.args[0])
        self.assertEqual(result["source"], "Genius")

    def test_official_genius_search_uses_token_when_available(self):
        player = play.Player.__new__(play.Player)
        search = {"response": {"hits": [{"result": {
            "title": "vampire",
            "url": "https://genius.com/Olivia-rodrigo-vampire-lyrics",
            "primary_artist": {"name": "Olivia Rodrigo"},
        }}]}}
        page = '<div data-lyrics-container="true">[Verse 1]<br>Words</div>'
        with (
            mock.patch.dict(play.os.environ, {"GENIUS_ACCESS_TOKEN": "secret"}),
            mock.patch.object(player, "_fetch_json", return_value=search) as fetch,
            mock.patch.object(player, "_fetch_text", return_value=page),
        ):
            result = player._fetch_genius_lyrics({
                "artist": "OR", "artist_tag": "OR",
                "artist_hint": "Olivia Rodrigo", "title": "vampire",
            })
        self.assertTrue(fetch.call_args.args[0].startswith(play.GENIUS_OFFICIAL_SEARCH_URL))
        self.assertEqual(fetch.call_args.args[1], {"Authorization": "Bearer secret"})
        self.assertEqual(result["text"], "[Verse 1]\nWords")

    def test_filename_metadata_prefers_artist_tag_then_dash_format(self):
        self.assertEqual(
            play.Player._lyrics_metadata_from_filename("[OR] vampire"),
            ("OR", "vampire"),
        )
        self.assertEqual(
            play.Player._lyrics_metadata_from_filename("Olivia Rodrigo - vampire"),
            ("Olivia Rodrigo", "vampire"),
        )
        self.assertEqual(
            play.Player._lyrics_metadata_from_filename("vampire"),
            ("", "vampire"),
        )

    def test_lyrics_cleanup_strips_timestamps_and_preserves_sections(self):
        cleaned = play.Player._clean_lyrics_text(
            "[ar:Artist]\n[00:01.20](Verse 1)\n[00:02.00]First line\n\n"
            "[00:04.00][Chorus]\n[00:05.00]Hook"
        )
        self.assertEqual(cleaned, "[Verse 1]\nFirst line\n\n[Chorus]\nHook")

    def test_lyrics_cleanup_removes_genius_preamble_and_control_junk(self):
        cleaned = play.Player._clean_lyrics_text(
            "\ufeff\x0062 ContributorsTranslationsEspañolDeutschSong Lyrics"
            "A page description… Read More\u00a0[Verse 1]\nFirst line"
        )
        self.assertEqual(cleaned, "[Verse 1]\nFirst line")

    def test_genius_title_matching_rejects_translation_and_remix_pages(self):
        score = play.Player._genius_title_score
        self.assertEqual(score("vampire", "vampire"), 100)
        self.assertEqual(score("vampire ft. Someone", "vampire"), 90)
        self.assertEqual(score("vampire (Spanish Translation)", "vampire"), 0)
        self.assertEqual(score("vampire Remix", "vampire"), 0)
        self.assertEqual(score("vampire diaries", "vampire"), 0)
        self.assertEqual(score("all-american bitch", "all american b-tch"), 100)

    def test_genius_retries_censored_title_with_verified_artist(self):
        player = play.Player.__new__(play.Player)
        search = {"response": {"sections": [{"type": "song", "hits": [{
            "result": {
                "title": "all-american bitch",
                "url": "https://genius.com/Olivia-rodrigo-all-american-bitch-lyrics",
                "primary_artist": {"name": "Olivia Rodrigo"},
            },
        }]}]}}
        page = '<div data-lyrics-container="true">[Verse 1]<br>Words</div>'
        with (
            mock.patch.object(player, "_fetch_json", side_effect=[
                {"response": {"sections": []}}, search,
            ]) as fetch,
            mock.patch.object(player, "_fetch_text", return_value=page),
        ):
            result = player._fetch_genius_lyrics({
                "artist": "OR", "artist_tag": "OR",
                "title": "all american b-tch",
            })
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("bitch+OR", fetch.call_args.args[0])
        self.assertEqual(result["source"], "Genius")
        self.assertEqual(result["text"], "[Verse 1]\nWords")

    def test_genius_outage_does_not_retry_every_censored_variant(self):
        player = play.Player.__new__(play.Player)
        with mock.patch.object(
            player, "_fetch_json", side_effect=TimeoutError("timed out")
        ) as fetch:
            result = player._fetch_genius_lyrics({
                "artist": "OR", "artist_tag": "OR",
                "title": "all american b-tch",
            })
        self.assertIsNone(result)
        fetch.assert_called_once()

    def test_genius_result_wins_when_artist_initials_match_tag(self):
        player = play.Player.__new__(play.Player)
        search = {
            "response": {
                "sections": [
                    {
                        "type": "song",
                        "hits": [
                            {
                                "result": {
                                    "title": "vampire",
                                    "url": "https://genius.com/Olivia-rodrigo-vampire-lyrics",
                                    "primary_artist": {"name": "Olivia Rodrigo"},
                                }
                            }
                        ],
                    }
                ]
            }
        }
        page = (
            '<div data-lyrics-container="true">[Verse 1]<br>'
            'I should have known it was strange</div>'
        )
        with (
            mock.patch.object(player, "_fetch_json", return_value=search),
            mock.patch.object(player, "_fetch_text", return_value=page),
        ):
            result = player._fetch_genius_lyrics(
                {"artist_tag": "OR", "title": "vampire"}
            )

        self.assertEqual(result["source"], "Genius")
        self.assertEqual(result["text"], "[Verse 1]\nI should have known it was strange")

    def test_genius_ranks_exact_title_above_unrelated_artist_result(self):
        player = play.Player.__new__(play.Player)
        search = {
            "response": {
                "sections": [{
                    "type": "song",
                    "hits": [
                        {"result": {
                            "title": "vampire diaries",
                            "url": "https://genius.com/wrong-title",
                            "primary_artist": {"name": "Olivia Rodrigo"},
                        }},
                        {"result": {
                            "title": "vampire",
                            "url": "https://genius.com/right",
                            "primary_artist": {"name": "Olivia Rodrigo"},
                        }},
                    ],
                }]
            }
        }
        page = '<div data-lyrics-container="true">[Verse 1]<br>Right</div>'
        with (
            mock.patch.object(player, "_fetch_json", return_value=search) as search_call,
            mock.patch.object(player, "_fetch_text", return_value=page) as page_call,
        ):
            result = player._fetch_genius_lyrics({
                "artist": "Olivia Rodrigo",
                "artist_tag": "OR",
                "title": "vampire",
            })

        self.assertIn("Olivia+Rodrigo", search_call.call_args.args[0])
        page_call.assert_called_once_with("https://genius.com/right")
        self.assertEqual(result["text"], "[Verse 1]\nRight")

    def test_genius_failure_falls_back_to_lrclib(self):
        player = play.Player.__new__(play.Player)
        expected = {"text": "Fallback", "source": "LRCLIB", "url": ""}
        with (
            mock.patch.object(
                player, "_fetch_genius_lyrics", side_effect=OSError("blocked")
            ),
            mock.patch.object(
                player, "_fetch_lrclib_lyrics", return_value=expected
            ) as lrclib,
        ):
            result = player._fetch_preferred_lyrics(
                {"artist_tag": "OR", "title": "vampire"}
            )

        self.assertIs(result, expected)
        lrclib.assert_called_once()

    def test_verified_genius_outranks_embedded_mp3_lyrics(self):
        player = play.Player.__new__(play.Player)
        genius = {"text": "Genius words", "source": "Genius", "url": "url"}
        with (
            mock.patch.object(player, "_fetch_genius_lyrics", return_value=genius),
            mock.patch.object(player, "_fetch_lrclib_lyrics") as lrclib,
        ):
            result = player._fetch_preferred_lyrics(
                {
                    "artist_tag": "OR",
                    "title": "vampire",
                    "lyrics": "Embedded words",
                }
            )

        self.assertIs(result, genius)
        lrclib.assert_not_called()

    def test_genius_rejects_artist_that_does_not_match_tag(self):
        player = play.Player.__new__(play.Player)
        search = {
            "response": {
                "sections": [
                    {
                        "type": "song",
                        "hits": [
                            {
                                "result": {
                                    "url": "https://genius.com/wrong",
                                    "primary_artist": {"name": "Wrong Artist"},
                                }
                            }
                        ],
                    }
                ]
            }
        }
        with (
            mock.patch.object(player, "_fetch_json", return_value=search),
            mock.patch.object(player, "_fetch_text") as fetch_page,
        ):
            result = player._fetch_genius_lyrics(
                {"artist_tag": "OR", "title": "vampire"}
            )

        self.assertIsNone(result)
        fetch_page.assert_not_called()

    def test_stale_lyrics_result_cannot_replace_new_song(self):
        player = play.Player.__new__(play.Player)
        player._lyrics_request_id = 2
        player.lyrics_song = "New"
        player._lyrics_loading = True
        player.lyrics_text = ""
        player.lyrics_source = ""
        player.lyrics_source_url = ""
        player.lyrics_status = "Finding lyrics…"
        player._lyrics_memory_cache = {}

        player._finish_lyrics_request(
            1, "Old", {"text": "Old words", "source": "LRCLIB"}
        )

        self.assertEqual(player.lyrics_song, "New")
        self.assertEqual(player.lyrics_text, "")
        self.assertTrue(player._lyrics_loading)

    def test_lrclib_falls_back_from_artist_alias_to_title_and_duration(self):
        player = play.Player.__new__(play.Player)
        player.lyrics_song = "[21P] Stressed Out"
        not_found = play.urllib.error.HTTPError(
            "https://lrclib.net/api/get", 404, "Not Found", {}, None
        )
        results = [
            {
                "trackName": "Stressed Out",
                "artistName": "Wrong Artist",
                "duration": 300,
                "plainLyrics": "Wrong",
            },
            {
                "trackName": "Stressed Out",
                "artistName": "Twenty One Pilots",
                "duration": 202,
                "plainLyrics": "[Verse 1]\nCorrect",
            },
        ]
        with (
            mock.patch.object(player, "_cached_duration_ms", return_value=202_000),
            mock.patch.object(
                player, "_fetch_json", side_effect=[not_found, [], results]
            ) as fetch,
        ):
            result = player._fetch_lrclib_lyrics(
                {
                    "artist": "21P",
                    "title": "Stressed Out",
                    "album": "",
                    "library_name": "[21P] Stressed Out",
                }
            )

        self.assertEqual(result["text"], "[Verse 1]\nCorrect")
        self.assertEqual(fetch.call_count, 3)

    def test_lrclib_rejects_same_title_and_duration_from_wrong_artist(self):
        player = play.Player.__new__(play.Player)
        player.lyrics_song = "[21P] Stressed Out"
        wrong = {
            "trackName": "Stressed Out", "artistName": "Twenty One Savage",
            "duration": 202, "plainLyrics": "Wrong",
        }
        with (
            mock.patch.object(player, "_cached_duration_ms", return_value=202_000),
            mock.patch.object(player, "_fetch_json", side_effect=[wrong, [wrong], [wrong]]),
        ):
            result = player._fetch_lrclib_lyrics({
                "artist": "21P", "artist_tag": "21P",
                "title": "Stressed Out", "album": "",
            })
        self.assertIsNone(result)

    def test_lrclib_retries_after_http_503_and_matches_censored_title(self):
        player = play.Player.__new__(play.Player)
        player.lyrics_song = "[OR] all american b-tch"
        unavailable = play.urllib.error.HTTPError(
            "https://lrclib.net/api/get", 503, "Service Unavailable", {}, None
        )
        results = [{
            "trackName": "all-american b*tch",
            "artistName": "Olivia Rodrigo",
            "duration": 164,
            "plainLyrics": "[Verse 1]\nWords",
        }]
        with (
            mock.patch.object(player, "_cached_duration_ms", return_value=164_000),
            mock.patch.object(player, "_fetch_json", side_effect=[
                unavailable, unavailable, results,
            ]) as fetch,
        ):
            result = player._fetch_lrclib_lyrics({
                "artist": "OR", "artist_tag": "OR",
                "title": "all american b-tch", "album": "",
            })
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(result["text"], "[Verse 1]\nWords")

    def test_lyrics_error_names_http_failure_instead_of_network_failure(self):
        player = play.Player.__new__(play.Player)
        player._lyrics_request_id = 1
        player.lyrics_song = "[OR] all american b-tch"
        player._lyrics_loading = True
        player._wake_ui = lambda: None
        error = play.urllib.error.HTTPError(
            "https://lrclib.net/api/search", 503, "Service Unavailable", {}, None
        )
        player._finish_lyrics_request(1, player.lyrics_song, None, error)
        self.assertIn("HTTP 503", player.lyrics_status)
        self.assertNotIn("could not be reached", player.lyrics_status)

    def test_failed_lyrics_lookup_can_retry_same_song(self):
        player = play.Player.__new__(play.Player)
        player._all_songs_set = {"[OR] all american b-tch"}
        player.lyrics_song = "[OR] all american b-tch"
        player.lyrics_status = "Lyrics provider returned HTTP 503."
        player.lyrics_text = ""
        player._lyrics_loading = False
        player._lyrics_request_id = 1
        player._lyrics_memory_cache = {}
        player.meta = {}
        with mock.patch.object(play.threading, "Thread") as thread:
            player.request_lyrics(player.lyrics_song)
        self.assertEqual(player._lyrics_request_id, 2)
        self.assertTrue(player._lyrics_loading)
        thread.return_value.start.assert_called_once()


@unittest.skipUnless(play.TEXTUAL_AVAILABLE, "Textual is not installed")
class TextualLayoutRegressionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def assert_inside(test_case, widget, width, height):
        region = widget.region
        test_case.assertGreaterEqual(region.x, 0)
        test_case.assertGreaterEqual(region.y, 0)
        test_case.assertLessEqual(region.right, width)
        test_case.assertLessEqual(region.bottom, height)

    async def test_idle_tick_refreshes_transport_without_rebuilding_table(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(100, 24)):
                    app.player.dirty = False
                    with (
                        mock.patch.object(app.player, "_process_pending"),
                        mock.patch.object(app.player, "_queue_apple_radio_poll"),
                        mock.patch.object(app.player, "_save_session_if_due"),
                        mock.patch.object(
                            app.player,
                            "_take_duration_ui_updates",
                            return_value=set(),
                        ),
                        mock.patch.object(app, "refresh_ui") as refresh_ui,
                        mock.patch.object(app, "refresh_transport") as transport,
                        mock.patch.object(app, "_refresh_duration_cells"),
                    ):
                        app.tick()

                    transport.assert_called_once_with()
                    refresh_ui.assert_not_called()

    async def test_playlist_resize_tracks_pointer_delta_and_preserves_library(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(100, 24)) as pilot:
                    sidebar = app.query_one("#playlists")
                    handle = app.query_one("#playlist-resizer")
                    initial_width = sidebar.region.width

                    handle.dragging = True
                    handle._drag_start_x = handle.region.x
                    handle._drag_start_width = initial_width
                    event = mock.Mock(screen_x=handle.region.x + 1)
                    await handle._on_mouse_move(event)
                    await pilot.pause()
                    self.assertEqual(sidebar.region.width, initial_width + 1)

                    app.player.current = "example"
                    app.player._all_songs_set.add("example")
                    app.player._playback_id += 1
                    with mock.patch.object(app.player, "request_lyrics"):
                        await pilot.click("#lyrics-tab")
                    await pilot.pause()
                    app.resize_playlist_sidebar(42)
                    await pilot.pause()
                    library = app.query_one("#library")
                    self.assertGreaterEqual(library.region.width, 30)
                    self.assertLess(sidebar.region.width, 42)

    async def test_library_border_fills_workspace_with_lyrics_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(100, 30)) as pilot:
                    for width, height in ((100, 30), (80, 24), (40, 12)):
                        await pilot.resize_terminal(width, height)
                        await pilot.pause()
                        workspace = app.query_one("#workspace")
                        library = app.query_one("#library")
                        self.assertEqual(library.region.bottom, workspace.content_region.bottom)
                        playlists = app.query_one("#playlists")
                        if playlists.region.height:
                            self.assertEqual(library.region.bottom, playlists.region.bottom)
                    await pilot.resize_terminal(100, 30)
                    await pilot.click("#lyrics-tab")
                    await pilot.pause()
                    self.assertEqual(
                        app.query_one("#library").region.bottom,
                        app.query_one("#lyrics-panel").region.bottom,
                    )

    async def test_lyrics_sidebar_only_fetches_when_opened_and_preserves_playlists(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "[OR] vampire.mp3").touch()
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(120, 28)) as pilot:
                    player = app.player

                    def provide_lyrics(name):
                        player.lyrics_song = name
                        player.lyrics_status = ""
                        player.lyrics_text = "[Verse 1]\nWords"
                        player.lyrics_source = "MP3 tags"
                        player.lyrics_source_url = ""

                    player.current = "[OR] vampire"
                    player._playback_id += 1
                    with mock.patch.object(
                        player, "request_lyrics", side_effect=provide_lyrics
                    ) as request:
                        app.refresh_ui()
                        await pilot.pause()
                        request.assert_not_called()
                        self.assertFalse(app.query_one("#workspace").has_class("lyrics-open"))
                        self.assertEqual(app.query_one("#lyrics-tab").region.width, 3)
                        await pilot.click("#lyrics-tab")
                        await pilot.pause()

                    request.assert_called_once_with("[OR] vampire")
                    self.assertTrue(app.query_one("#workspace").has_class("lyrics-open"))
                    self.assertEqual(app.query_one("#lyrics-tab").region.width, 0)
                    self.assertIn(
                        "[Verse 1]", str(app.query_one("#lyrics-content").render())
                    )
                    panel = app.query_one("#lyrics-panel")
                    resizer = app.query_one("#lyrics-resizer")
                    playlists = app.query_one("#playlists")
                    playlist_width = playlists.region.width
                    self.assertGreater(resizer.region.width, 0)
                    self.assertNotEqual(
                        app.query_one("#library").styles.border_right[0], "solid"
                    )
                    right_edge = panel.region.right
                    app.resize_lyrics_sidebar(right_edge - 48)
                    await pilot.pause()
                    self.assertEqual(panel.region.width, 48)
                    self.assertEqual(playlists.region.width, playlist_width)
                    app.resize_lyrics_sidebar(right_edge - 2)
                    await pilot.pause()
                    self.assertEqual(panel.region.width, 22)
                    self.assertEqual(playlists.region.width, playlist_width)
                    await pilot.click("#lyrics-close")
                    await pilot.pause()
                    self.assertFalse(app.query_one("#workspace").has_class("lyrics-open"))
                    self.assertEqual(app.query_one("#lyrics-tab").region.width, 3)
                    self.assertEqual(resizer.region.width, 0)
                    self.assertEqual(
                        app.query_one("#library").styles.border_right[0], "solid"
                    )
                    player._playback_id += 1
                    with mock.patch.object(player, "request_lyrics") as request:
                        app.refresh_ui()
                        await pilot.pause()
                        request.assert_not_called()
                        await pilot.click("#lyrics-tab")
                        await pilot.pause()
                        request.assert_called_once_with("[OR] vampire")

    async def test_new_song_lyrics_start_at_top_without_interrupting_manual_scroll(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "First.mp3").touch()
            Path(folder, "Second.mp3").touch()
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(120, 28)) as pilot:
                    player = app.player
                    long_lyrics = "\n".join(f"Lyric line {i}" for i in range(80))

                    def request_lyrics(name):
                        player.lyrics_text = long_lyrics if name == "First" else ""
                        player.lyrics_status = "Finding lyrics…" if name == "Second" else ""

                    player.current = "First"
                    player._playback_id += 1
                    with mock.patch.object(player, "request_lyrics", side_effect=request_lyrics):
                        await pilot.click("#lyrics-tab")
                        await pilot.pause()
                        scroll = app.query_one("#lyrics-scroll")
                        scroll.scroll_to(y=1000, animate=False, force=True)
                        await pilot.pause()
                        self.assertGreater(scroll.scroll_y, 0)

                        player.current = "Second"
                        player._playback_id += 1
                        app.refresh_ui()
                        await pilot.pause()
                        self.assertEqual(scroll.scroll_y, 0)

                        player.lyrics_text = long_lyrics
                        player.lyrics_status = ""
                        app.refresh_ui()
                        await pilot.pause()
                        self.assertEqual(scroll.scroll_y, 0)

                        scroll.scroll_to(y=1000, animate=False, force=True)
                        await pilot.pause()
                        self.assertGreater(scroll.scroll_y, 0)
                        app.refresh_ui()
                        await pilot.pause()
                        self.assertGreater(scroll.scroll_y, 0)

    async def test_youtube_preview_hides_lyrics_and_restores_open_sidebar(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "First.mp3").touch()
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(120, 28)) as pilot:
                    player = app.player
                    player.current = "First"
                    player._playback_id += 1

                    def request_lyrics(name):
                        player.lyrics_text = "[Verse 1]\nWords" if name else ""
                        player.lyrics_status = ""

                    with mock.patch.object(
                        player, "request_lyrics", side_effect=request_lyrics
                    ) as request:
                        await pilot.click("#lyrics-tab")
                        await pilot.pause()
                        self.assertGreater(app.query_one("#lyrics-panel").region.width, 0)
                        self.assertEqual(request.call_args.args, ("First",))

                        player._youtube_loading = True
                        app._sync_lyrics_panel()
                        await pilot.pause()
                        self.assertEqual(app.query_one("#lyrics-panel").region.width, 0)
                        self.assertEqual(app.query_one("#lyrics-tab").region.width, 0)
                        self.assertEqual(request.call_args.args, ("",))
                        self.assertEqual(player.lyrics_text, "")

                        player._youtube_loading = False
                        player._youtube_temp_path = "/tmp/preview.mp3"
                        player.current = "YouTube: Example"
                        app._sync_lyrics_panel()
                        await pilot.pause()
                        self.assertEqual(app.query_one("#lyrics-panel").region.width, 0)
                        self.assertEqual(request.call_count, 2)

                        player._youtube_temp_path = None
                        player.current = "First"
                        app._sync_lyrics_panel()
                        await pilot.pause()
                        self.assertGreater(app.query_one("#lyrics-panel").region.width, 0)
                        self.assertEqual(request.call_args.args, ("First",))

    async def test_sidebar_widths_reload_from_library_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(120, 28)) as pilot:
                    app.resize_playlist_sidebar(31, persist=True)
                    await pilot.click("#lyrics-tab")
                    await pilot.pause()
                    panel = app.query_one("#lyrics-panel")
                    app.resize_lyrics_sidebar(panel.region.right - 45, persist=True)
                    await pilot.pause()
                    self.assertEqual(app.query_one("#playlists").region.width, 31)
                    self.assertEqual(panel.region.width, 45)

                cache = json.loads(Path(folder, play.CACHE_FILE).read_text())
                self.assertEqual(cache["settings"]["layout"], {
                    "playlists": 31, "lyrics": 45,
                })

                reopened = play.MusicApp(folder)
                async with reopened.run_test(size=(120, 28)) as pilot:
                    await pilot.pause()
                    self.assertEqual(reopened.query_one("#playlists").region.width, 31)
                    await pilot.click("#lyrics-tab")
                    await pilot.pause()
                    self.assertEqual(reopened.query_one("#lyrics-panel").region.width, 45)
                    await pilot.resize_terminal(80, 28)
                    await pilot.pause()
                    self.assertLess(reopened.query_one("#lyrics-panel").region.width, 45)
                    await pilot.resize_terminal(120, 28)
                    await pilot.pause()
                    self.assertEqual(reopened.query_one("#lyrics-panel").region.width, 45)

    async def test_modals_and_main_layout_survive_extreme_resizes(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(100, 32)) as pilot:
                    app.action_show_help()
                    await pilot.pause()
                    self.assert_inside(
                        self, app.screen.query_one("#help-box"), 100, 32
                    )

                    # Resizing while a modal is current must still update the
                    # underlying player screen's responsive layout.
                    await pilot.resize_terminal(40, 12)
                    await pilot.pause()
                    self.assert_inside(
                        self, app.screen.query_one("#help-box"), 40, 12
                    )
                    await pilot.press("escape")
                    await pilot.pause()
                    self.assertTrue(
                        {"narrow", "tiny", "short", "very-short"}.issubset(
                            app.screen.classes
                        )
                    )
                    self.assertEqual(app.query_one("#editor").region.height, 0)
                    self.assert_inside(self, app.query_one("#library"), 40, 12)

                    menus = (
                        (play.PlaylistMenuScreen("P", 999, 999), "#playlist-menu"),
                        (play.SongMenuScreen(["Song"], 999, 999, "P"), "#song-menu"),
                        (play.AppleRadioMenuScreen(999, 999), "#apple-radio-menu"),
                    )
                    for screen, selector in menus:
                        await pilot.resize_terminal(100, 32)
                        app.push_screen(screen)
                        await pilot.pause()
                        await pilot.resize_terminal(20, 8)
                        await pilot.pause()
                        self.assert_inside(
                            self, app.screen.query_one(selector), 20, 8
                        )
                        await pilot.press("escape")
                        await pilot.pause()

    async def test_sort_does_not_scroll_to_playing_row_and_preview_is_highlighted(self):
        with tempfile.TemporaryDirectory() as folder:
            for index in range(100):
                Path(folder, f"Track {index:03}.mp3").touch()
            with mock.patch.object(play.Player, "start_background_services"):
                app = play.MusicApp(folder)
                async with app.run_test(size=(100, 24)) as pilot:
                    player = app.player
                    table = app.query_one("#table")
                    player.current = "Track 010"
                    app.refresh_ui(rebuild_table=True)
                    await pilot.pause()
                    table.scroll_to(y=0, animate=False, force=True)
                    await pilot.pause()

                    # Click the Name header. Descending order puts the playing
                    # song near the bottom, but the viewport must remain at 0.
                    await pilot.click("#table", offset=(12, 0))
                    await pilot.pause()
                    await pilot.pause()
                    self.assertTrue(player.sort_reverse)
                    self.assertEqual(table.scroll_y, 0)
                    self.assertEqual(table.cursor_row, 0)
                    self.assertEqual(
                        table._music_highlighted_row_key, "Track 010"
                    )

                    player.youtube_preview_enabled = True
                    player.youtube_results = [
                        {"id": "a", "title": "First", "channel": "One"},
                        {"id": "b", "title": "Second", "channel": "Two"},
                    ]
                    # Simulate stats being added after preview selection; the
                    # stable video id should still identify the active row.
                    player._youtube_active_result = {
                        "id": "b", "title": "Second", "channel": "Two"
                    }
                    player.youtube_results[1]["views"] = 123
                    player._youtube_loading = True
                    app.refresh_ui(rebuild_table=True)
                    await pilot.pause()
                    self.assertEqual(
                        table._music_highlighted_row_key, "youtube-1"
                    )
                    rendered = table._render_cell(1, 1, Style(), 20)
                    backgrounds = {
                        segment.style.bgcolor.name
                        for line in rendered
                        for segment in line
                        if segment.style and segment.style.bgcolor
                    }
                    self.assertIn("#222222", backgrounds)


if __name__ == "__main__":
    unittest.main()
