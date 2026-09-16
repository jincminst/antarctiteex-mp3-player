import argparse
import html.parser
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import tempfile
import textwrap
import urllib.parse
import urllib.error
import urllib.request
import unicodedata
from types import SimpleNamespace
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from better_profanity import profanity

profanity.load_censor_words(whitelist_words=["hell"])
profanity.add_censor_words(["sexy", "sexier", "sexiest", "sexiness", "sexting"])

_MASKED_WORD = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[\*✱✲✳✴✵✶✷✸✹✺✻✼✽✾•·_\-–—!]+)+[A-Za-z0-9]+(?![A-Za-z0-9])"
)
_MASKED_LETTERS = str.maketrans({
    "✱": "*", "✲": "*", "✳": "*", "✴": "*", "✵": "*", "✶": "*",
    "✷": "*", "✸": "*", "✹": "*", "✺": "*", "✻": "*", "✼": "*",
    "✽": "*", "✾": "*", "•": "*", "·": "*", "_": "*",
    "-": "*", "–": "*", "—": "*", "!": "i",
})


def _censor_lyrics_text(value):
    """Mask profanity, including common lyric spellings with substitute glyphs."""
    def censor_masked(match):
        normalized = match.group().translate(_MASKED_LETTERS)
        return "****" if profanity.censor(normalized) == "****" else match.group()

    return profanity.censor(_MASKED_WORD.sub(censor_masked, value))

try:
    from rich.cells import cell_len, set_cell_size
except ImportError:
    # Player's pure helpers are also usable in environments where the optional
    # Textual/Rich UI isn't installed (including the lightweight test runner).
    def _fallback_cell_width(character):
        if unicodedata.combining(character) or unicodedata.category(character) in {"Cf", "Mn", "Me"}:
            return 0
        return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1

    def cell_len(text):
        return sum(_fallback_cell_width(character) for character in str(text))

    def set_cell_size(text, width):
        output = []
        used = 0
        for character in str(text):
            character_width = _fallback_cell_width(character)
            if used + character_width > width:
                break
            output.append(character)
            used += character_width
        return "".join(output) + " " * max(0, width - used)

# The playback/library engine still contains a few legacy key and styling
# constants used by its pure helper methods.  Keeping them as plain values
# avoids importing curses; the running interface below is entirely Textual.
curses = SimpleNamespace(
    A_BOLD=1, A_DIM=2, A_NORMAL=0, A_REVERSE=4, A_UNDERLINE=8,
    COLOR_BLACK=0, COLOR_RED=1, COLOR_WHITE=7,
    KEY_BACKSPACE=263, KEY_BTAB=353, KEY_DC=330, KEY_DOWN=258,
    KEY_END=360, KEY_HOME=262, KEY_LEFT=260, KEY_NPAGE=338,
    KEY_PPAGE=339, KEY_RESIZE=410, KEY_RIGHT=261, KEY_UP=259,
    error=Exception,
    color_pair=lambda _n: 0, curs_set=lambda *_: None,
    doupdate=lambda: None, init_pair=lambda *_: None,
    mousemask=lambda *_: None, start_color=lambda: None,
    ungetch=lambda *_: None, use_default_colors=lambda: None,
)

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.geometry import Offset
    from textual.message import Message
    from textual.screen import ModalScreen
    from textual.widgets import Button, DataTable, Input, Label, Static
    from rich.text import Text
    TEXTUAL_AVAILABLE = True
except ImportError:
    App = ModalScreen = object
    TEXTUAL_AVAILABLE = False

try:
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
    import pygame
except ImportError:
    print("Error: Missing dependencies. Run: pip install pygame")
    sys.exit(1)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# The command-line entry point resolves this to the directory it is launched
# from. Keeping it as "." also gives callers a useful default after the module
# has been installed into site-packages.
DEFAULT_MP3_FOLDER = "."
LOCAL_YT_DLP = os.path.join(APP_DIR, ".yt-dlp-venv", "bin", "yt-dlp")
YT_DLP_BIN = LOCAL_YT_DLP if os.path.exists(LOCAL_YT_DLP) else "yt-dlp"
AUDIO_BUFFER_SIZE = 8192
INITIAL_INTERNAL_VOL = 0.4
CACHE_FILE = ".mp3cache.json"
CACHE_VERSION = 3
SESSION_SAVE_INTERVAL_S = 5.0
LEGACY_META_FILE = ".mp3meta.json"
LEGACY_PLAYLISTS_FILE = ".mp3playlists.json"
LEGACY_ANALYSIS_FILE = ".mp3analysis.json"
# Kept as aliases for callers that imported the old names.
META_FILE = CACHE_FILE
PLAYLISTS_FILE = CACHE_FILE
VOLUME_MULTIPLIER_VERSION = "peak-minus-3db-v1"
VOLUME_TARGET_PEAK_DB = -3.0

UI_REFRESH_MS_ACTIVE = 500
UI_REFRESH_MS_IDLE = 2000
UI_REFRESH_MS_INPUT = 1
TEXTUAL_TICK_S = 1.0
VOLUME_ENFORCE_S = 1.0
PLAYBACK_POLL_S = 1.0
DURATION_REDRAW_S = 1.0
SCREEN_LOCK_POLL_S = 2.0
OUTPUT_SAFETY_ACTIVE_POLL_S = 2.0
OUTPUT_SAFETY_IDLE_POLL_S = 10.0
APPLE_RADIO_POLL_S = 5.0
YT_PREVIEW_AUDIO_QUALITY = "0"
YT_PREVIEW_FORMAT = "ba[ext=m4a]/ba[ext=mp3]/ba[ext=ogg]/ba[acodec^=mp4a]/ba[acodec^=opus]/ba/b"
YT_DLP_CONCURRENT_FRAGMENTS = "4"
YT_DLP_RETRIES = "2"
YT_DLP_COOKIES_FROM_BROWSER = "edge"
YT_DLP_JS_RUNTIME = "quickjs"
YT_DLP_IMPERSONATE = "chrome"
YT_DOWNLOAD_FORMAT = "ba/b"
YT_STATS_WORKERS = 3
YT_SEARCH_RESULT_LIMIT = 75
YT_SEARCH_CHANNEL_LIMIT = 8
LYRICS_API_URL = "https://lrclib.net/api"
GENIUS_SEARCH_URL = "https://genius.com/api/search/multi"
GENIUS_OFFICIAL_SEARCH_URL = "https://api.genius.com/search"
LYRICS_HTTP_TIMEOUT_S = 8
LYRICS_CACHE_VERSION = 2
APPLE_RADIO_STATIONS = (
    {
        "name": "Apple Music 1",
        "tagline": "The new music that matters.",
        "url": "https://music.apple.com/us/station/apple-music-1/ra.978194965",
    },
    {
        "name": "Apple Music Hits",
        "tagline": "Songs you know and love.",
        "url": "https://music.apple.com/us/station/apple-music-hits/ra.1498155548",
    },
    {
        "name": "Apple Music Country",
        "tagline": "Where it sounds like home.",
        "url": "https://music.apple.com/us/station/apple-music-country/ra.1498157166",
    },
)


class _GeniusLyricsParser(html.parser.HTMLParser):
    """Extract text from Genius' data-lyrics-container elements."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.lines = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div" and attributes.get("data-lyrics-container") == "true":
            if self.lines and self.lines[-1] != "\n":
                self.lines.append("\n")
            self.depth = 1
            return
        if not self.depth:
            return
        if tag == "div":
            self.depth += 1
        elif tag == "br":
            self.lines.append("\n")

    def handle_endtag(self, tag):
        if self.depth and tag == "div":
            self.depth -= 1
            if not self.depth:
                self.lines.append("\n")

    def handle_data(self, data):
        if self.depth:
            self.lines.append(data)

    def text(self):
        return "".join(self.lines)


class Player:
    """UI-independent music library and playback engine."""

    def __init__(self, stdscr=None, folder=DEFAULT_MP3_FOLDER):
        self.stdscr = stdscr
        self.folder = folder
        self._cache_lock = threading.RLock()
        self._cache = self._load_cache()
        self.analysis = self._cache["analysis"]
        settings = self._cache.get("settings", {})
        saved_layout = settings.get("layout", {})
        self.layout_widths = {}
        if isinstance(saved_layout, dict):
            for pane, minimum, maximum in (
                ("playlists", 8, 42), ("lyrics", 8, 64)
            ):
                width = saved_layout.get(pane)
                if isinstance(width, int) and not isinstance(width, bool):
                    self.layout_widths[pane] = max(minimum, min(maximum, width))
        saved_session = settings.get("session", {})
        if not isinstance(saved_session, dict):
            saved_session = {}

        pygame.mixer.init(44100, -16, 2, AUDIO_BUFFER_SIZE)

        self.all_songs = []
        self.songs = []
        self.play_pool = []
        self.play_tab = "All"
        self.current = "None"
        self.status_msg = ""
        self.scroll = 0
        self.paused = False
        try:
            self.vol = min(
                1.0,
                max(0.0, float(saved_session.get("volume", INITIAL_INTERNAL_VOL))),
            )
        except (TypeError, ValueError):
            self.vol = INITIAL_INTERNAL_VOL
        self.running = True
        self._shutting_down = False
        self.focused = "list"
        self.search = ""
        self.search_cursor = 0
        self.download_rename_path = ""
        self.download_rename_buf = ""
        self.download_rename_cursor = 0
        self.mp3_rename_old = ""
        self.mp3_rename_buf = ""
        self.mp3_rename_cursor = 0
        self.delete_confirm_songs = []
        self.youtube_preview_enabled = False
        self.apple_radio_enabled = False
        self.apple_radio_active = ""
        self.apple_radio_now = ""
        self.apple_radio_music_now = ""
        self._apple_radio_last_poll = 0.0
        self._apple_radio_polling = False
        self._pre_radio_song = ""
        self._pre_radio_pos_ms = 0
        self._pre_radio_paused = False
        self._pre_radio_play_tab = "All"
        self.sort_mode = "name"
        self.sort_reverse = False
        self.weight_sort_mode = "chance"
        self.weight_sort_reverse = True
        saved_mode = saved_session.get("play_mode")
        self.play_mode = saved_mode if saved_mode in {"loop", "shuffle"} else "loop"
        self.song_switch_locked = False
        self.show_shuffle_weights = False
        self.col_width = 1
        self.listen_col_width = 8
        self.duration_col_width = 5

        self.playlists = self._load_playlists()
        self.playlist_tags = self._load_playlist_tags()
        saved_hints = settings.get("artist_hints", {})
        self.artist_hints = (
            {
                tag.upper(): artist
                for tag, artist in saved_hints.items()
                if isinstance(tag, str) and isinstance(artist, str)
                and tag.strip() and artist.strip()
            }
            if isinstance(saved_hints, dict) else {}
        )
        self.tab_names = ["All"]
        self._rebuild_tab_names()
        self.active_tab = 0
        saved_playlist = saved_session.get("playlist")
        if saved_playlist in self.tab_names:
            self.active_tab = self.tab_names.index(saved_playlist)

        self.ctx_menu_open = False
        self.ctx_menu_type = None
        self.ctx_menu_song = None
        self.ctx_menu_songs = []
        self.ctx_menu_items = []
        self.ctx_menu_y = 0
        self.ctx_menu_x = 0
        self.ctx_menu_sel = 0
        self.ctx_menu_scroll = 0
        # Store the adjusted draw position so click detection matches
        self._ctx_draw_y = 0
        self._ctx_draw_x = 0
        self._ctx_draw_vis = 0
        self._ctx_draw_w = 0

        self.dialog_mode = None
        self.dialog_buf = ""
        self.dialog_cursor = 0
        self.dialog_context = None
        self._dialog_cancel_region = None
        self._rename_cancel_region = None
        self._scroll_dragging = False
        self._scroll_drag_offset = 0
        self._text_cursor_target = None

        self.rename_active = False
        self.rename_old_name = ""
        self.rename_buf = ""
        self.rename_cursor = 0

        self.confirm_open = False
        self.confirm_msg = ""
        self.confirm_action = None
        self.confirm_cancel_action = None
        self.confirm_sel = 1
        self._confirm_btn_regions = []
        self._confirm_y = 0
        self._confirm_x = 0
        self._confirm_w = 0
        self._confirm_h = 0

        self._song_len_ms = 0
        self._current_playback_path = ""
        self._play_start = 0.0
        self._pause_offset = 0.0
        self._pause_start = 0.0
        self._last_loop_idx = 0
        self._playback_id = 0
        self._completion_suppressed_until = 0.0
        self._current_volume_multiplier = 1.0
        self._natural_loop_restart = False

        self._plays_cache = {}
        self._duration_ms_cache = {}
        self._total_listen_hours_cache = {}
        self._duration_load_queue = deque()
        self._duration_load_pending = set()
        self._duration_ui_updates = set()
        self._duration_load_lock = threading.Lock()
        self._duration_load_wakeup = threading.Event()
        self._volume_multiplier_lock = threading.Lock()
        self._volume_analysis_lock = threading.Lock()
        self._volume_multiplier_pending = set()
        self._song_lower_cache = {}
        self._all_songs_set = set()
        self._meta_dirty = False
        self.meta = self._load_meta()
        self._sync_total_listen_hours()
        self._rebuild_plays_cache()

        self._pending_play_incs = {}
        self._play_inc_lock = threading.Lock()
        self._pending_shuffle_next = None
        self._pending_media_toggle = False
        self._last_media_button_at = 0.0
        self._media_button_lock = threading.Lock()
        self._last_volume_apply_at = 0.0
        self._pending_youtube_result = None
        self._pending_youtube_error = None
        self._pending_youtube_search_result = None
        self._pending_youtube_search_error = None
        self._pending_youtube_stats_result = None
        self._pending_youtube_stats_error = None
        self._pending_youtube_download_result = None
        self._pending_youtube_download_error = None
        self.youtube_download_progress = None
        self._pending_preview_cleanup = False
        self._text_kill_ring = ""

        self.lyrics_song = ""
        self.lyrics_status = ""
        self.lyrics_text = ""
        self.lyrics_source = ""
        self.lyrics_source_url = ""
        self._lyrics_loading = False
        self._lyrics_request_id = 0
        self._lyrics_memory_cache = {}

        self._youtube_loading = False
        self._youtube_request_id = 0
        self._youtube_download_loading = False
        self._youtube_download_request_id = 0
        self._youtube_results_loading = False
        self._youtube_stats_loading = False
        self._youtube_search_request_id = 0
        self._youtube_stats_request_id = 0
        self._youtube_search_debounce_at = 0.0
        self._youtube_search_pending_query = ""
        self._youtube_results_query = ""
        self._youtube_search_cache = {}
        self._youtube_view = "search"
        self._youtube_channel_title = ""
        self._youtube_channel_url = ""
        self.youtube_sort_mode = "title"
        self.youtube_sort_reverse = False
        self.youtube_results = []
        self._youtube_active_result = None
        self._youtube_temp_dir = None
        self._youtube_temp_path = None
        self._youtube_search_proc = None
        self._youtube_preview_proc = None
        self._youtube_download_proc = None

        self._screen_locked = False
        self._paused_by_lock = False
        self._paused_by_speaker_safety = False
        self._media_key_proc = None
        self.non_speaker_mode = True
        self.show_tags = settings.get("show_tags", True) is not False
        self._saved_session = saved_session
        self._last_session_save_at = time.monotonic()
        self._last_session_snapshot = None
        self._current_output_device = ""
        self._output_safety_wakeup = threading.Event()
        self._safety_lock = threading.Lock()
        self._find_my_log_proc = None
        self._background_services_started = False
        self._background_services_lock = threading.Lock()

        self.dirty = True
        self._is_active = False
        self._ui_wakeup = None
        self._ui_notify = None
        self._needs_redraw_hdr = True
        self._needs_redraw_tabs = True
        self._needs_redraw_bar = True
        self._needs_redraw_lst = True
        self._needs_redraw_inp = True
        self._needs_redraw_ctx = True
        self._needs_redraw_confirm = True
        self._last_draw_time = 0.0

        self._tab_regions = []
        self._plus_btn_region = None
        self._tab_row_y = 0

        self._mode_region = None
        self._mode_label_region = None
        self._weights_region = None
        self._apple_radio_region = None
        self._sort_region = None
        self._vol_down_region = None
        self._vol_up_region = None
        self._clear_selection_region = None
        self._sort_col_regions = []
        self._num_col_region = None
        self._yt_preview_toggle_region = None
        self._youtube_back_region = None
        self.selected_songs = set()
        self._selection_anchor = None

        if self.stdscr is not None:
            self._setup_curses()
            self._layout()
        self._rebuild()
        self._restore_saved_session()
        self._start_duration_loader()

        threading.Thread(target=self._watch_playback, daemon=True).start()

    def start_background_services(self):
        """Start optional OS integrations without delaying the first UI paint."""
        with self._background_services_lock:
            if self._background_services_started:
                return
            self._background_services_started = True

        def worker():
            self._start_media_key_listener()
            self._start_screen_lock_watcher()
            self._start_output_safety_watcher()
            self._start_find_my_alert_watcher()

        threading.Thread(target=worker, daemon=True).start()

    def _playlists_path(self):
        return self._cache_path()

    def _load_playlists(self):
        cache = getattr(self, "_cache", None)
        if cache is None:
            cache = self._load_cache()
            self._cache = cache
        data = cache.get("playlists", {})
        playlists = {}
        if not isinstance(data, dict):
            return playlists
        for name, songs in data.items():
            if not isinstance(name, str):
                continue
            name = name.strip()
            if not name or name == "All":
                continue
            if not isinstance(songs, list):
                songs = []
            clean = []
            seen = set()
            for song in songs:
                if not isinstance(song, str) or song in seen:
                    continue
                clean.append(song)
                seen.add(song)
            playlists[name] = clean
        return playlists

    def _load_playlist_tags(self):
        cache = getattr(self, "_cache", None)
        if cache is None:
            cache = self._load_cache()
            self._cache = cache
        data = cache.get("playlist_tags", {})
        if not isinstance(data, dict):
            return {}
        tags = {}
        for name, tag in data.items():
            if not isinstance(name, str) or not isinstance(tag, str):
                continue
            name = name.strip()
            tag = tag.strip()
            if name and tag and "[" not in tag and "]" not in tag:
                tags[name] = tag
        return tags

    def _playlist_tag(self, playlist_name):
        return str(getattr(self, "playlist_tags", {}).get(playlist_name, "")).strip()

    def _playlist_song_names(self, playlist_name):
        """Return dynamic tag matches or the playlist's manually added songs."""
        tag = self._playlist_tag(playlist_name)
        if tag:
            prefix = f"[{tag}]".casefold()
            return [name for name in self.all_songs if name.casefold().startswith(prefix)]
        members = set(self.playlists.get(playlist_name, []))
        return [name for name in self.all_songs if name in members]

    def _set_playlist_tag(self, playlist_name, value):
        if playlist_name not in self.playlists:
            return False
        tag = str(value).strip()
        if "[" in tag or "]" in tag:
            self.status_msg = "Enter only the artist tag, without square brackets"
            self._emit_notification(self.status_msg, severity="error")
            return False
        if any(ord(character) < 32 for character in tag) or len(tag) > 40:
            self.status_msg = "Artist tag must be 40 characters or fewer"
            self._emit_notification(self.status_msg, severity="error")
            return False
        if not hasattr(self, "playlist_tags"):
            self.playlist_tags = {}
        if tag:
            self.playlist_tags[playlist_name] = tag
        else:
            self.playlist_tags.pop(playlist_name, None)
        self._save_playlists()
        if self.play_tab == playlist_name:
            self._rebuild_play_pool()
        self._rebuild()
        self._needs_redraw_tabs = True
        self.dirty = True
        if tag:
            count = len(self._playlist_song_names(playlist_name))
            self._emit_notification(
                f"'{playlist_name}' now finds [{tag}] automatically ({count} MP3{'s' if count != 1 else ''})"
            )
        else:
            self._emit_notification(f"'{playlist_name}' is now a manual playlist")
        return True

    def _save_playlists(self):
        self._save_cache()

    def _emit_notification(self, message, severity="information", timeout=4):
        callback = getattr(self, "_ui_notify", None)
        if callable(callback):
            try:
                callback(message, severity=severity, timeout=timeout)
            except Exception:
                pass

    def _rebuild_tab_names(self, preserve_name=None):
        if preserve_name is None:
            preserve_name = "All"
            try:
                if 0 <= self.active_tab < len(self.tab_names):
                    preserve_name = self.tab_names[self.active_tab]
            except Exception:
                pass
        names = sorted(self.playlists.keys(), key=str.lower)
        self.tab_names = ["All"] + names
        if preserve_name in self.tab_names:
            self.active_tab = self.tab_names.index(preserve_name)
        else:
            self.active_tab = 0

    def _current_tab_name(self):
        active_tab = getattr(self, "active_tab", 0)
        tab_names = getattr(self, "tab_names", ["All"])
        if 0 <= active_tab < len(tab_names):
            return tab_names[active_tab]
        return "All"

    def _create_playlist(self, name):
        name = name.strip()
        if not name or name == "All":
            return
        if name not in self.playlists:
            active_name = self._current_tab_name()
            self.playlists[name] = []
            self._save_playlists()
            self._rebuild_tab_names(active_name)
            self._needs_redraw_tabs = True
            self.dirty = True
            self._emit_notification(f"Created playlist '{name}'")

    def _delete_playlist(self, name):
        if name in self.playlists:
            active_name = self._current_tab_name()
            del self.playlists[name]
            getattr(self, "playlist_tags", {}).pop(name, None)
            self._save_playlists()
            self._rebuild_tab_names(active_name if active_name != name else "All")
            if self.play_tab == name:
                self.play_tab = "All"
                self._rebuild_play_pool()
            self._rebuild()
            self._needs_redraw_tabs = True
            self._needs_redraw_hdr = True
            self.dirty = True
            self._emit_notification(f"Deleted playlist '{name}'")

    def _rename_playlist(self, old_name, new_name):
        new_name = new_name.strip()
        if not new_name or new_name == "All" or old_name not in self.playlists:
            return
        if new_name == old_name:
            return
        if new_name in self.playlists:
            self.status_msg = f"Playlist '{new_name}' already exists"
            self._needs_redraw_hdr = True
            return
        songs = list(self.playlists.pop(old_name))
        self.playlists[new_name] = songs
        if old_name in getattr(self, "playlist_tags", {}):
            self.playlist_tags[new_name] = self.playlist_tags.pop(old_name)
        self._save_playlists()
        self._rebuild_tab_names(new_name)
        if getattr(self, "play_tab", "All") == old_name:
            self.play_tab = new_name
            self._needs_redraw_hdr = True
        self._rebuild()
        self._needs_redraw_tabs = True
        self.dirty = True
        self._emit_notification(f"Renamed playlist to '{new_name}'")

    def _add_song_to_playlist(self, playlist_name, song_name):
        if playlist_name not in self.playlists:
            return False
        if self._playlist_tag(playlist_name):
            self._emit_notification(
                f"'{playlist_name}' is filled automatically by its artist tag",
                severity="warning",
            )
            return False
        if song_name not in self.playlists[playlist_name]:
            self.playlists[playlist_name].append(song_name)
            self._save_playlists()
            if self.play_tab == playlist_name:
                self._rebuild_play_pool()
            self._rebuild()
            self._emit_notification(f"Added '{song_name}' to '{playlist_name}'")
            return True
        return False

    def _remove_song_from_playlist(self, playlist_name, song_name):
        if playlist_name not in self.playlists:
            return False
        if self._playlist_tag(playlist_name):
            self._emit_notification(
                f"Edit the tag on '{playlist_name}' to change its songs",
                severity="warning",
            )
            return False
        if song_name in self.playlists[playlist_name]:
            self.playlists[playlist_name].remove(song_name)
            self._save_playlists()
            if self.play_tab == playlist_name:
                self._rebuild_play_pool()
            self._rebuild()
            self._emit_notification(f"Removed '{song_name}' from '{playlist_name}'")
            return True
        return False

    def _add_songs_to_playlist(self, playlist_name, song_names):
        if playlist_name not in self.playlists:
            return 0
        if self._playlist_tag(playlist_name):
            self._emit_notification(
                f"'{playlist_name}' is filled automatically by its artist tag",
                severity="warning",
            )
            return 0
        added = 0
        playlist = self.playlists[playlist_name]
        for song_name in song_names:
            if song_name not in playlist:
                playlist.append(song_name)
                added += 1
        if added:
            self._save_playlists()
            if self.play_tab == playlist_name:
                self._rebuild_play_pool()
            self._rebuild()
            noun = "song" if added == 1 else "songs"
            self._emit_notification(
                f"Added {added} {noun} to '{playlist_name}'"
            )
        return added

    def _remove_songs_from_playlist(self, playlist_name, song_names):
        if playlist_name not in self.playlists:
            return 0
        if self._playlist_tag(playlist_name):
            self._emit_notification(
                f"Edit the tag on '{playlist_name}' to change its songs",
                severity="warning",
            )
            return 0
        remove_set = set(song_names)
        playlist = self.playlists[playlist_name]
        kept = [song for song in playlist if song not in remove_set]
        removed = len(playlist) - len(kept)
        if removed:
            self.playlists[playlist_name] = kept
            self._save_playlists()
            if self.play_tab == playlist_name:
                self._rebuild_play_pool()
            self._rebuild()
            noun = "song" if removed == 1 else "songs"
            self._emit_notification(
                f"Removed {removed} {noun} from '{playlist_name}'"
            )
        return removed

    def _sort_songs(self, songs):
        plays = self._plays_cache
        lower_cache = self._song_lower_cache

        def lower_name(name):
            return lower_cache.get(name) or name.lower()

        if self.sort_mode == "name":
            songs.sort(key=lower_name, reverse=self.sort_reverse)
        elif self.sort_mode == "plays":
            songs.sort(
                key=lambda x: (plays.get(x, 0), lower_name(x)),
                reverse=self.sort_reverse,
            )
        elif self.sort_mode == "duration":
            songs.sort(
                key=lambda x: (self._cached_duration_ms(x), lower_name(x)),
                reverse=self.sort_reverse,
            )
        elif self.sort_mode == "last":
            songs.sort(
                key=lambda x: (self._last_played_at(x), lower_name(x)),
                reverse=self.sort_reverse,
            )
        else:
            songs.sort(
                key=lambda x: (self._total_listen_hours(x), lower_name(x)),
                reverse=self.sort_reverse,
            )

    def _weighted_shuffle_choice(self, songs):
        weighted = self._shuffle_weight_rows(songs)
        if not weighted:
            return None
        choices = [row["name"] for row in weighted]
        weights = [row["weight"] for row in weighted]
        if len(choices) == 1:
            return choices[0]
        return random.choices(choices, weights=weights, k=1)[0]

    def _shuffle_weight_candidates(self):
        if self.songs:
            return list(self.songs)
        if self.play_pool:
            return list(self.play_pool)
        if self.play_tab == "All":
            return list(self.all_songs)
        return self._playlist_song_names(self.play_tab)

    def _shuffle_weight_rows_for_view(self):
        rows = self._shuffle_weight_rows(
            self._shuffle_weight_candidates(), exclude_current=False
        )
        self._sort_shuffle_weight_rows(rows)
        ordered = [row["name"] for row in rows]
        if ordered != self.songs:
            self.songs = ordered
        return rows

    def _sort_shuffle_weight_rows(self, rows):
        mode = self.weight_sort_mode
        reverse = self.weight_sort_reverse
        if mode == "name":
            key = lambda row: row["name"].lower()
        elif mode == "plays":
            key = lambda row: (row.get("plays", 0), row["name"].lower())
        elif mode == "z":
            key = lambda row: (row.get("z", 0.0), row["name"].lower())
        elif mode == "weight":
            key = lambda row: (row.get("weight", 0.0), row["name"].lower())
        else:
            key = lambda row: (row.get("probability", 0.0), row["name"].lower())
        rows.sort(key=key, reverse=reverse)

    def _shuffle_weight_rows(self, songs, exclude_current=True):
        songs = list(songs)
        if not songs:
            return []
        if (
            exclude_current
            and len(songs) > 1
            and self.current not in ("None", "Loading...")
        ):
            songs = [name for name in songs if name != self.current] or songs

        counts = [max(0, int(self._plays_cache.get(name, 0) or 0)) for name in songs]
        mean = sum(counts) / len(counts)
        variance = sum((count - mean) ** 2 for count in counts) / len(counts)
        stddev = math.sqrt(variance)
        if stddev <= 0:
            rows = [
                {
                    "name": name,
                    "plays": count,
                    "z": 0.0,
                    "weight": 1.0,
                }
                for name, count in zip(songs, counts)
            ]
        else:
            rows = []
            for name, count in zip(songs, counts):
                z = (count - mean) / stddev
                # Negative z means less-played, so it gets a larger weight.
                rows.append(
                    {
                        "name": name,
                        "plays": count,
                        "z": z,
                        "weight": math.exp(max(-3.0, min(3.0, -z))),
                    }
                )
        total_weight = sum(row["weight"] for row in rows) or 1.0
        for row in rows:
            row["probability"] = row["weight"] / total_weight
        return rows

    def _rebuild_play_pool(self):
        if self.play_tab == "All":
            pool = list(self.all_songs)
        else:
            pool = self._playlist_song_names(self.play_tab)
        self._sort_songs(pool)
        self.play_pool = pool

    def _use_viewed_playlist_for_playback(self):
        """Make the playlist on screen the source for subsequent playback."""
        self.play_tab = self._current_tab_name()
        self._pending_shuffle_next = None
        self._rebuild_play_pool()
        self._needs_redraw_hdr = True
        self.dirty = True
        return self.play_tab

    def _show_confirm(self, msg, action, cancel_action=None):
        self.confirm_open = True
        self.confirm_msg = msg
        self.confirm_action = action
        self.confirm_cancel_action = cancel_action
        self.confirm_sel = 1
        self._confirm_btn_regions = []
        self._needs_redraw_confirm = True
        self.dirty = True

    def _confirm_yes(self):
        action = self.confirm_action
        self.confirm_open = False
        self.confirm_msg = ""
        self.confirm_action = None
        self.confirm_cancel_action = None
        self.confirm_sel = 1
        self._confirm_btn_regions = []
        self._mark_all_dirty()
        if action:
            action()

    def _confirm_no(self):
        cancel_action = getattr(self, "confirm_cancel_action", None)
        self.confirm_open = False
        self.confirm_msg = ""
        self.confirm_action = None
        self.confirm_cancel_action = None
        self.confirm_sel = 1
        self._confirm_btn_regions = []
        self._mark_all_dirty()
        if cancel_action:
            cancel_action()

    def _start_screen_lock_watcher(self):
        watcher = None
        try:
            import Quartz

            def _watch_quartz():
                while self.running:
                    time.sleep(SCREEN_LOCK_POLL_S)
                    try:
                        d = Quartz.CGSessionCopyCurrentDictionary()
                        locked = bool(d and d.get("CGSSessionScreenIsLocked", False))
                    except Exception:
                        locked = False
                    self._handle_lock_state(locked)

            watcher = _watch_quartz
        except ImportError:
            pass
        if watcher is None:
            try:
                import ctypes
                import ctypes.util

                path = ctypes.util.find_library("ApplicationServices")
                if path:
                    appserv = ctypes.cdll.LoadLibrary(path)
                    appserv.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
                    cf = ctypes.cdll.LoadLibrary(
                        ctypes.util.find_library("CoreFoundation")
                    )
                    cf.CFRelease.argtypes = [ctypes.c_void_p]
                    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
                    cf.CFStringCreateWithCString.argtypes = [
                        ctypes.c_void_p,
                        ctypes.c_char_p,
                        ctypes.c_uint32,
                    ]
                    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
                    cf.CFDictionaryGetValue.argtypes = [
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                    ]
                    cf.CFBooleanGetValue.restype = ctypes.c_bool
                    cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
                    cf.CFGetTypeID.restype = ctypes.c_ulong
                    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
                    cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
                    cf.CFNumberGetValue.restype = ctypes.c_bool
                    cf.CFNumberGetValue.argtypes = [
                        ctypes.c_void_p,
                        ctypes.c_int,
                        ctypes.c_void_p,
                    ]
                    cf.CFNumberGetTypeID.restype = ctypes.c_ulong
                    kCFStringEncodingUTF8 = 0x08000100
                    key_cfstr = cf.CFStringCreateWithCString(
                        None, b"CGSSessionScreenIsLocked", kCFStringEncodingUTF8
                    )

                    def _watch_ctypes():
                        while self.running:
                            time.sleep(SCREEN_LOCK_POLL_S)
                            locked = False
                            try:
                                d = appserv.CGSessionCopyCurrentDictionary()
                                if d:
                                    val = cf.CFDictionaryGetValue(d, key_cfstr)
                                    if val:
                                        type_id = cf.CFGetTypeID(val)
                                        if type_id == cf.CFBooleanGetTypeID():
                                            locked = cf.CFBooleanGetValue(val)
                                        elif type_id == cf.CFNumberGetTypeID():
                                            out = ctypes.c_int(0)
                                            cf.CFNumberGetValue(
                                                val, 9, ctypes.byref(out)
                                            )
                                            locked = out.value != 0
                                    cf.CFRelease(d)
                            except Exception:
                                locked = False
                            self._handle_lock_state(locked)

                    watcher = _watch_ctypes
            except Exception:
                pass
        if watcher is None:
            try:

                def _watch_ioreg():
                    while self.running:
                        time.sleep(SCREEN_LOCK_POLL_S)
                        try:
                            out = subprocess.check_output(
                                ["ioreg", "-n", "Root", "-d1", "-w0"],
                                stderr=subprocess.DEVNULL,
                                text=True,
                                timeout=5,
                            )
                            locked = "CGSSessionScreenIsLocked" in out
                        except Exception:
                            locked = False
                        self._handle_lock_state(locked)

                watcher = _watch_ioreg
            except Exception:
                pass
        if watcher is not None:
            threading.Thread(target=watcher, daemon=True).start()

    def _handle_lock_state(self, locked):
        if locked and not self._screen_locked:
            self._screen_locked = True
            if self.current not in ("None", "Loading...") and not self.paused:
                self._paused_by_lock = True
                self._pending_media_toggle = True
        elif not locked and self._screen_locked:
            self._screen_locked = False
            if self._paused_by_lock and self.paused:
                self._paused_by_lock = False
                self._pending_media_toggle = True
            else:
                self._paused_by_lock = False

    def _wake_ui(self):
        if callable(self._ui_wakeup):
            try:
                self._ui_wakeup()
            except RuntimeError:
                # A UI-button safety check is already on Textual's app thread;
                # call_from_thread is only valid for the background watchers.
                pass
            return
        try:
            curses.ungetch(0)
        except Exception:
            pass

    def _start_media_key_listener(self):
        # On macOS, headset/earphone center buttons arrive as native
        # systemDefined consumer-key events. Run both available paths because
        # some headset drivers expose the control only to one of them.
        if sys.platform == "darwin":
            self._start_macos_media_key_listener()
        try:
            import contextlib
            import io

            with contextlib.redirect_stderr(io.StringIO()):
                from pynput import keyboard

                def on_press(key):
                    try:
                        if key == keyboard.Key.media_play_pause:
                            self._queue_media_button_toggle()
                    except Exception:
                        pass

                listener = keyboard.Listener(on_press=on_press)
                listener.daemon = True
                listener.start()
            return
        except Exception:
            pass

    def _queue_media_button_toggle(self):
        """Queue one toggle while deduplicating the same hardware press."""
        now = time.monotonic()
        with self._media_button_lock:
            if now - self._last_media_button_at < 0.35:
                return False
            self._last_media_button_at = now
            self._pending_media_toggle = True
        self._wake_ui()
        return True

    def _start_macos_media_key_listener(self):
        try:
            swift_code = 'import Cocoa\nNSEvent.addGlobalMonitorForEvents(matching: .systemDefined) { event in\n    if event.subtype.rawValue == 8 {\n        let keyCode = (event.data1 & 0xFFFF0000) >> 16\n        let keyFlags = event.data1 & 0x0000FFFF\n        let keyState = ((keyFlags & 0xFF00) >> 8) == 0xA\n        if keyCode == 16 && keyState { print("PLAYPAUSE"); fflush(stdout) }\n    }\n}\nRunLoop.main.run()\n'
            proc = subprocess.Popen(
                ["swift", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self._media_key_proc = proc
            proc.stdin.write(swift_code)
            proc.stdin.close()

            def reader():
                while self.running:
                    try:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        if "PLAYPAUSE" in line:
                            self._queue_media_button_toggle()
                    except Exception:
                        break

            threading.Thread(target=reader, daemon=True).start()
            return True
        except Exception:
            return False

    @staticmethod
    def _is_builtin_speaker_name(name):
        normalized = str(name or "").strip().lower()
        return normalized in {"built-in output", "mac speakers"} or (
            "speaker" in normalized
            and any(word in normalized for word in ("built-in", "macbook", "imac", "mac "))
        )

    @staticmethod
    def _is_find_my_alert_log(line):
        text = str(line or "").lower()
        source = any(token in text for token in (
            "findmy", "find my", "fmfd", "findmydeviced",
        ))
        alert = any(token in text for token in (
            "play sound", "playsound", "play_sound", "sound alert", "alert sound",
        ))
        return source and alert

    def _halt_for_speaker_safety(self, force_enable=False):
        """Immediately silence playback; safe to call from watcher threads."""
        with self._safety_lock:
            if force_enable:
                self.non_speaker_mode = True
                self._output_safety_wakeup.set()
            if self.current in ("None", "Loading...") or self.paused:
                self.dirty = True
                self._wake_ui()
                return
            self._pause_start = time.monotonic()
            try:
                pygame.mixer.music.pause()
            except Exception:
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
            self.paused = True
            self._paused_by_speaker_safety = True
            self.status_msg = "Paused: built-in speaker safety"
            self.dirty = True
            self._needs_redraw_hdr = True
            self._needs_redraw_bar = True
        self._wake_ui()

    def _resume_from_speaker_safety(self):
        """Resume only audio that speaker safety itself paused."""
        with self._safety_lock:
            if (
                not self._paused_by_speaker_safety
                or not self.non_speaker_mode
                or not self.paused
                or self.current in ("None", "Loading...")
                or self._is_builtin_speaker_name(self._current_output_device)
            ):
                return False
            self._paused_by_speaker_safety = False
            self._pause_offset += time.monotonic() - self._pause_start
            try:
                pygame.mixer.music.unpause()
                self._apply_volume(force=True)
            except Exception:
                pass
            self.paused = False
            self.status_msg = "Resumed: earphones connected"
            self.dirty = True
            self._needs_redraw_hdr = True
            self._needs_redraw_bar = True
        self._wake_ui()
        return True

    def _handle_output_device(self, name):
        name = str(name or "").strip()
        previous = self._current_output_device
        self._current_output_device = name
        is_speaker = self._is_builtin_speaker_name(name)
        if (
            name
            and name != previous
            and self.non_speaker_mode
            and is_speaker
        ):
            self._halt_for_speaker_safety()
        elif (
            name
            and name != previous
            and self.non_speaker_mode
            and not is_speaker
            and self._paused_by_speaker_safety
        ):
            self._resume_from_speaker_safety()

    def _start_output_safety_watcher(self):
        if sys.platform != "darwin":
            return
        switch_audio = shutil.which("SwitchAudioSource")
        if not switch_audio:
            return

        def watch_output():
            while self.running and not self._shutting_down:
                # With safety disabled there is nothing to enforce. Sleeping
                # on an event avoids continuously spawning a command-line
                # helper in an otherwise idle player; the toggle wakes this
                # thread immediately when safety is enabled again.
                if not self.non_speaker_mode:
                    self._output_safety_wakeup.wait(timeout=60.0)
                    self._output_safety_wakeup.clear()
                    continue
                try:
                    result = subprocess.run(
                        [switch_audio, "-c", "-t", "output"],
                        capture_output=True, text=True, timeout=1,
                    )
                    if result.returncode == 0:
                        self._handle_output_device(result.stdout)
                except Exception:
                    pass
                active = (
                    self.current not in ("None", "Loading...")
                    and not self.paused
                )
                interval = (
                    OUTPUT_SAFETY_ACTIVE_POLL_S
                    if active
                    else OUTPUT_SAFETY_IDLE_POLL_S
                )
                self._output_safety_wakeup.wait(timeout=interval)
                self._output_safety_wakeup.clear()

        threading.Thread(target=watch_output, daemon=True).start()

    def _start_find_my_alert_watcher(self):
        if sys.platform != "darwin" or not shutil.which("log"):
            return
        try:
            proc = subprocess.Popen(
                [
                    "/usr/bin/log", "stream", "--style", "ndjson", "--level", "info",
                    "--predicate",
                    'process CONTAINS[c] "findmy" OR process == "fmfd" OR senderImagePath CONTAINS[c] "findmy"',
                ],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            self._find_my_log_proc = proc

            def read_find_my_log():
                while self.running and not self._shutting_down:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    if self._is_find_my_alert_log(line):
                        self._halt_for_speaker_safety(force_enable=True)

            threading.Thread(target=read_find_my_log, daemon=True).start()
        except Exception:
            self._find_my_log_proc = None

    def _meta_path(self):
        return self._cache_path()

    def _cache_path(self):
        return os.path.join(self.folder, CACHE_FILE)

    @staticmethod
    def _empty_cache():
        return {
            "version": CACHE_VERSION,
            "songs": {},
            "playlists": {},
            "playlist_tags": {},
            "analysis": {},
            "settings": {},
        }

    @staticmethod
    def _read_json(path, default):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default

    def _load_cache(self):
        path = self._cache_path()
        if os.path.exists(path):
            data = self._read_json(path, {})
            if isinstance(data, dict) and any(
                key in data
                for key in (
                    "songs", "playlists", "playlist_tags", "analysis", "settings"
                )
            ):
                cache = self._empty_cache()
                cache["version"] = data.get("version", CACHE_VERSION)
                for key in (
                    "songs", "playlists", "playlist_tags", "analysis", "settings"
                ):
                    value = data.get(key, {})
                    cache[key] = value if isinstance(value, dict) else {}
                return cache

            # Accept a bare legacy object at the new path. This also keeps old
            # callers that imported META_FILE or PLAYLISTS_FILE working.
            if isinstance(data, dict):
                cache = self._empty_cache()
                if data and all(isinstance(value, list) for value in data.values()):
                    cache["playlists"] = data
                else:
                    cache["songs"] = data
                return cache

        cache = self._empty_cache()
        legacy = {
            "songs": LEGACY_META_FILE,
            "playlists": LEGACY_PLAYLISTS_FILE,
            "analysis": LEGACY_ANALYSIS_FILE,
        }
        found_legacy = False
        for key, filename in legacy.items():
            legacy_path = os.path.join(self.folder, filename)
            if not os.path.exists(legacy_path):
                continue
            found_legacy = True
            value = self._read_json(legacy_path, {})
            if isinstance(value, dict):
                cache[key] = value

        if found_legacy and self._write_cache(cache):
            for filename in legacy.values():
                try:
                    os.remove(os.path.join(self.folder, filename))
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        return cache

    def _load_meta(self):
        cache = getattr(self, "_cache", None)
        if cache is None:
            cache = self._load_cache()
            self._cache = cache
        data = cache.get("songs", {})
        return data if isinstance(data, dict) else {}

    def _scan_song_names(self):
        names = []
        try:
            for f in os.listdir(self.folder):
                if f.lower().endswith(".mp3"):
                    names.append(f[:-4])
        except Exception:
            pass
        return sorted(names, key=str.lower)

    def _export_mp3_names(self):
        try:
            names = self._scan_song_names()
            path = os.path.join(self.folder, "mp3_names.txt")
            with open(path, "w") as f:
                f.write("\n".join(names))
                if names:
                    f.write("\n")
            self.status_msg = f"Exported {len(names)} MP3 name{'s' if len(names) != 1 else ''}"
            self._emit_notification(self.status_msg)
        except Exception as exc:
            self.status_msg = f"Export failed: {str(exc)[:120]}"
            self._emit_notification(self.status_msg, severity="error")
        self._needs_redraw_hdr = True
        self.dirty = True

    def _refresh_song_lower_cache(self):
        self._song_lower_cache = {name: name.lower() for name in self.all_songs}
        self._all_songs_set = set(self.all_songs)

    def _sync_total_listen_hours(self):
        for name, entry in list(self.meta.items()):
            if not isinstance(entry, dict):
                continue
            plays = self._extract_count(entry)
            try:
                total_ms = int(entry.get("total_listen_ms", 0) or 0)
            except (TypeError, ValueError):
                total_ms = 0
            if total_ms > 0:
                hours = round(total_ms / 3600000, 2)
            else:
                try:
                    duration_seconds = float(entry.get("duration_seconds", 0) or 0)
                except (TypeError, ValueError):
                    duration_seconds = 0
                if duration_seconds <= 0:
                    try:
                        duration_ms = int(entry.get("duration_ms", 0) or 0)
                    except (TypeError, ValueError):
                        duration_ms = 0
                    duration_seconds = duration_ms / 1000 if duration_ms > 0 else 0
                if duration_seconds <= 0:
                    continue
                hours = round((plays * duration_seconds) / 3600, 2)
            self._total_listen_hours_cache[name] = (plays, hours)

    def _save_meta(self):
        self._save_cache()

    def _write_cache(self, cache):
        try:
            os.makedirs(self.folder, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                prefix=f"{CACHE_FILE}.", suffix=".tmp", dir=self.folder
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(cache, f, indent=2)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, self._cache_path())
            except Exception:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                raise
            return True
        except Exception:
            return False

    def _save_cache(self):
        lock = getattr(self, "_cache_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._cache_lock = lock
        with lock:
            previous = getattr(self, "_cache", self._empty_cache())
            previous_settings = previous.get("settings", {})
            if not isinstance(previous_settings, dict):
                previous_settings = {}
            cache = {
                "version": CACHE_VERSION,
                "songs": getattr(self, "meta", previous.get("songs", {})).copy(),
                "playlists": getattr(
                    self, "playlists", previous.get("playlists", {})
                ).copy(),
                "playlist_tags": getattr(
                    self, "playlist_tags", previous.get("playlist_tags", {})
                ).copy(),
                "analysis": getattr(
                    self, "analysis", previous.get("analysis", {})
                ).copy(),
                "settings": {
                    **previous_settings,
                    "show_tags": getattr(self, "show_tags", True),
                    "artist_hints": getattr(self, "artist_hints", {}).copy(),
                    "layout": getattr(self, "layout_widths", {}).copy(),
                    "session": self._session_snapshot(),
                },
            }
            if self._write_cache(cache):
                self._cache = cache

    def _session_snapshot(self):
        """Return the small, restart-safe part of the playback state."""
        previous = getattr(self, "_cache", {}).get("settings", {})
        previous_session = previous.get("session", {}) if isinstance(previous, dict) else {}
        if not isinstance(previous_session, dict):
            previous_session = {}
        session = dict(previous_session)
        try:
            session["volume"] = round(min(1.0, max(0.0, float(self.vol))), 2)
        except (AttributeError, TypeError, ValueError):
            session["volume"] = INITIAL_INTERNAL_VOL
        mode = getattr(self, "play_mode", "loop")
        session["play_mode"] = mode if mode in {"loop", "shuffle"} else "loop"
        try:
            session["playlist"] = self._current_tab_name()
        except Exception:
            session["playlist"] = "All"

        current = getattr(self, "current", "None")
        songs = getattr(self, "_all_songs_set", set())
        if current in songs:
            position_ms = self._current_pos_in_song()[0]
            if position_ms <= 0 and getattr(self, "_play_start", 0):
                position_ms = self._elapsed_ms()
            session["song"] = current
            session["position_ms"] = max(0, int(position_ms))
        elif current in {"None", "Loading..."}:
            session["song"] = ""
            session["position_ms"] = 0
        return session

    def _save_session_if_due(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_session_save_at < SESSION_SAVE_INTERVAL_S:
            return False
        snapshot = self._session_snapshot()
        self._last_session_save_at = now
        if not force and snapshot == self._last_session_snapshot:
            return False
        self._last_session_snapshot = snapshot
        self._save_cache()
        return True

    def _restore_saved_session(self):
        """Load the last local song at its saved position and leave it paused."""
        session = getattr(self, "_saved_session", {})
        if not isinstance(session, dict):
            return False
        name = session.get("song")
        if not isinstance(name, str) or name not in self._all_songs_set:
            return False
        path = self._song_path(name)
        try:
            position_ms = max(0, int(session.get("position_ms", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            position_ms = 0
        duration_ms = self._cached_duration_ms(name)
        if duration_ms > 0:
            position_ms = min(position_ms, max(0, duration_ms - 1000))
        try:
            self._current_volume_multiplier = self._cached_volume_multiplier(name)
            self._current_playback_path = path
            self._song_len_ms = duration_ms
            pygame.mixer.music.load(path)
            self._apply_volume(force=True)
            start_s = position_ms / 1000.0
            try:
                pygame.mixer.music.play(loops=0, start=start_s)
            except Exception:
                pygame.mixer.music.play(loops=0)
                if position_ms:
                    pygame.mixer.music.set_pos(start_s)
            pygame.mixer.music.pause()
            now = time.monotonic()
            self.current = name
            self.paused = True
            self._play_start = now - start_s
            self._pause_start = now
            self._pause_offset = 0.0
            self.status_msg = f"Restored '{name}.mp3' (paused)"
            self._last_session_snapshot = self._session_snapshot()
            return True
        except Exception as exc:
            self.current = "None"
            self.paused = False
            self._song_len_ms = 0
            self._current_playback_path = ""
            self._set_playback_error(name, exc, notify=False)
            return False

    @staticmethod
    def _extract_count(entry):
        if isinstance(entry, dict):
            for k in ("play_count", "plays", "count"):
                v = entry.get(k)
                if v is not None:
                    try:
                        return max(0, int(v))
                    except (TypeError, ValueError, OverflowError):
                        return 0
            return 0
        if isinstance(entry, int):
            return max(0, entry)
        return 0

    def _rebuild_plays_cache(self):
        cache = {}
        for k, v in self.meta.items():
            cache[k] = self._extract_count(v)
        self._plays_cache = cache

    def _plays(self, name):
        return self._plays_cache.get(name, 0)

    def _last_played_at(self, name):
        entry = self.meta.get(name, {})
        if not isinstance(entry, dict):
            return 0
        for key in ("last_played_at", "last_play_count_at"):
            try:
                value = int(entry.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                return value
        return 0

    def _fmt_since_last_play(self, name):
        last_at = self._last_played_at(name)
        if last_at <= 0:
            return "--"
        elapsed = max(0, int(time.time()) - last_at)
        days = elapsed // 86400
        hours = (elapsed % 86400) // 3600
        if days > 0:
            return f"{days}d {hours}h"
        return f"{hours}h"

    def _song_path(self, name):
        return os.path.join(self.folder, name + ".mp3")

    @staticmethod
    def _lyrics_metadata_from_filename(name):
        """Return an artist/title fallback from the library filename."""
        label = str(name or "").strip()
        tagged = re.match(r"^\[([^\]]+)\]\s*(.+)$", label)
        if tagged:
            return tagged.group(1).strip(), tagged.group(2).strip()
        if " - " in label:
            artist, title = label.split(" - ", 1)
            if artist.strip() and title.strip():
                return artist.strip(), title.strip()
        return "", label

    @staticmethod
    def _artist_tag_from_filename(name):
        tagged = re.match(r"^\[([^\]]+)\]", str(name or "").strip())
        return tagged.group(1).strip() if tagged else ""

    def _playlist_artist_for_tag(self, tag):
        """Find an artist from a tagged playlist or a verified cached hint."""
        tag = str(tag or "").strip()
        for name, playlist_tag in getattr(self, "playlist_tags", {}).items():
            if str(playlist_tag).casefold() == tag.casefold():
                return name
        return getattr(self, "artist_hints", {}).get(tag.upper(), "")

    def _remember_artist_for_tag(self, tag, artist):
        if not tag or not artist:
            return
        hints = getattr(self, "artist_hints", None)
        if hints is None:
            hints = self.artist_hints = {}
        if hints.get(tag.upper()) != artist:
            hints[tag.upper()] = artist
            self._meta_dirty = True

    def _infer_artist_for_tag(self, tag, current_name):
        """Identify an abbreviation from a clearer sibling song in the library."""
        siblings = [
            name for name in getattr(self, "all_songs", [])
            if name != current_name
            and self._artist_tag_from_filename(name).casefold() == tag.casefold()
        ]
        embedded_artists = set()
        for name in siblings:
            artist, _title = self._lyrics_metadata_from_filename(name)
            metadata = self._read_audio_lyrics_metadata(name)
            embedded_artist = str(metadata.get("artist") or "").strip()
            if embedded_artist and embedded_artist.casefold() != artist.casefold():
                embedded_artists.add(embedded_artist)
        if len(embedded_artists) == 1:
            artist = embedded_artists.pop()
            self._remember_artist_for_tag(tag, artist)
            return artist

        # Try a few distinctive titles, rather than searching the ambiguous
        # current title with initials that Genius may treat as unrelated words.
        siblings.sort(
            key=lambda name: (
                bool(re.search(r"\d", self._lyrics_metadata_from_filename(name)[1])),
                len(self._lyrics_metadata_from_filename(name)[1].split()) > 1,
                len(name),
            ),
            reverse=True,
        )
        for name in siblings[:4]:
            _artist, title = self._lyrics_metadata_from_filename(name)
            try:
                payload = self._search_genius(title)
            except Exception:
                break
            found = set()
            for hit in self._genius_song_hits(payload):
                result = hit.get("result", {}) if isinstance(hit, dict) else {}
                artist = str(result.get("primary_artist", {}).get("name") or "").strip()
                if (
                    self._genius_title_score(result.get("title", ""), title) == 100
                    and self._artist_matches_tag(artist, tag)
                ):
                    found.add(artist)
            if len(found) == 1:
                artist = found.pop()
                self._remember_artist_for_tag(tag, artist)
                return artist
        return ""

    @staticmethod
    def _artist_matches_tag(artist, tag):
        """Return whether an artist name matches a bracketed artist tag."""
        def normalized(value):
            value = unicodedata.normalize("NFKD", str(value or ""))
            return re.sub(r"[^a-z0-9]", "", value.casefold())

        wanted = normalized(tag)
        if not wanted:
            return False
        words = re.findall(
            r"[A-Za-z0-9]+",
            unicodedata.normalize("NFKD", str(artist or "")),
        )
        initials = "".join(word[0] for word in words if word)
        return wanted in {normalized(artist), normalized(initials)}

    @staticmethod
    def _normalized_lyrics_identity(value):
        """Normalize song/artist text for provider result matching."""
        value = unicodedata.normalize("NFKD", str(value or "")).casefold()
        return " ".join(re.findall(r"[a-z0-9]+", value))

    @staticmethod
    def _lyrics_title_variants(title):
        """Try the usual missing vowels in a censored word such as b-tch."""
        title = str(title or "").strip()
        variants = [title]
        censored = re.search(r"\b([A-Za-z])[-*]([A-Za-z]{2,5})\b", title)
        if censored:
            for vowel in "iueao":
                expanded = (
                    title[:censored.start()]
                    + censored.group(1) + vowel + censored.group(2)
                    + title[censored.end():]
                )
                if expanded not in variants:
                    variants.append(expanded)
        return variants

    @classmethod
    def _genius_title_score(cls, candidate, wanted):
        """Score a Genius title without confusing translations for originals."""
        candidate = cls._normalized_lyrics_identity(candidate)
        wanted_variants = [
            cls._normalized_lyrics_identity(variant)
            for variant in cls._lyrics_title_variants(wanted)
        ]
        if not candidate or not any(wanted_variants):
            return 0
        # Treat hyphens between words as optional: "all-american" and
        # "all american" are the same title for matching purposes.
        if candidate.replace(" ", "") in {
            variant.replace(" ", "") for variant in wanted_variants
        }:
            return 100

        extra_version = re.compile(
            r"\b(?:romanized|translation|translated|live|remix|edit|remaster(?:ed)?|"
            r"instrumental|karaoke|demo|acoustic|sped up|slowed)\b"
        )
        for wanted_variant in wanted_variants:
            if candidate.startswith(wanted_variant + " "):
                suffix = candidate[len(wanted_variant):].strip()
                if extra_version.search(suffix):
                    return 0
                # Genius sometimes includes a featured artist in the title.
                if suffix.startswith(("feat ", "featuring ", "ft ")):
                    return 90
        return 0

    @staticmethod
    def _clean_lyrics_text(value):
        """Normalize provider text while retaining its section headings."""
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\ufeff", "").replace("\u00a0", " ")
        text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        text = re.sub(r"^(?:ï»¿|ÿþ|þÿ)+", "", text)

        # Genius occasionally puts its contributor count, translation menu,
        # description and "Read More" control inside the lyrics container.
        # When a real section marker follows that unmistakable preamble, start
        # at the marker rather than displaying the page chrome as lyrics.
        section = re.search(
            r"\[(?:verse|pre[- ]?chorus|chorus|post[- ]?chorus|bridge|intro|"
            r"outro|refrain|hook|break|interlude)(?:[^\]]*)\]",
            text,
            re.I,
        )
        if section:
            preamble = text[:section.start()]
            if re.search(
                r"\bcontributors?\b|translations?|read\s+more",
                preamble,
                re.I,
            ):
                text = text[section.start():]
        # Convert synced LRC to readable lyrics. Metadata lines such as [ar:]
        # are omitted, while structural labels such as [Chorus] remain.
        lines = []
        for raw_line in text.splitlines():
            line = re.sub(
                r"^(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\])+\s*",
                "",
                raw_line,
            )
            if re.match(r"^\[(?:ar|al|ti|by|offset|length|re):", line, re.I):
                continue
            line = re.sub(
                r"^\((verse|pre[- ]?chorus|chorus|post[- ]?chorus|bridge|intro|outro|refrain|hook)([^)]*)\)$",
                lambda match: f"[{match.group(1).title()}{match.group(2)}]",
                line.strip(),
                flags=re.I,
            )
            lines.append(line.rstrip())
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        compact = []
        for line in lines:
            if line or not compact or compact[-1]:
                compact.append(line)
        return "\n".join(compact).strip()

    def _read_audio_lyrics_metadata(self, name):
        """Read embedded song identity and lyrics without another dependency."""
        artist, title = self._lyrics_metadata_from_filename(name)
        metadata = {
            "artist": artist,
            "artist_tag": self._artist_tag_from_filename(name),
            "title": title,
            "album": "",
            "lyrics": "",
        }
        if metadata["artist_tag"]:
            metadata["artist_hint"] = self._playlist_artist_for_tag(
                metadata["artist_tag"]
            )
        path = self._song_path(name)
        if not shutil.which("ffprobe") or not os.path.exists(path):
            return metadata
        try:
            proc = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format_tags",
                    "-of", "json", path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
                check=False,
            )
            payload = json.loads(proc.stdout or "{}")
            tags = payload.get("format", {}).get("tags", {})
            if not isinstance(tags, dict):
                return metadata
            lowered = {str(key).casefold(): value for key, value in tags.items()}
            metadata["artist"] = str(lowered.get("artist") or artist).strip()
            metadata["title"] = str(lowered.get("title") or title).strip()
            metadata["album"] = str(lowered.get("album") or "").strip()
            for key, value in lowered.items():
                if key in {
                    "lyrics", "unsyncedlyrics", "syncedlyrics"
                } or key.startswith("lyrics-"):
                    cleaned = self._clean_lyrics_text(value)
                    if cleaned:
                        metadata["lyrics"] = cleaned
                        break
        except Exception:
            pass
        return metadata

    @staticmethod
    def _fetch_json(url, headers=None):
        request_headers = {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://genius.com/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        }
        request_headers.update(headers or {})
        request = urllib.request.Request(
            url,
            headers=request_headers,
        )
        with urllib.request.urlopen(request, timeout=LYRICS_HTTP_TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _fetch_text(url):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            },
        )
        with urllib.request.urlopen(request, timeout=LYRICS_HTTP_TIMEOUT_S) as response:
            return response.read().decode("utf-8", errors="replace")

    def _search_genius(self, search_text):
        query = urllib.parse.urlencode({"q": search_text})
        token = os.environ.get("GENIUS_ACCESS_TOKEN", "").strip()
        if token:
            return self._fetch_json(
                f"{GENIUS_OFFICIAL_SEARCH_URL}?{query}",
                {"Authorization": f"Bearer {token}"},
            )
        return self._fetch_json(f"{GENIUS_SEARCH_URL}?{query}")

    @staticmethod
    def _genius_song_hits(payload):
        response = payload.get("response", {})
        hits = list(response.get("hits", []))
        for section in response.get("sections", []):
            if isinstance(section, dict) and section.get("type") == "song":
                hits.extend(section.get("hits", []))
        return hits

    def _fetch_genius_lyrics(self, metadata):
        """Find the best exact-title Genius result for the known artist."""
        tag = str(metadata.get("artist_tag") or "").strip()
        known_artist = str(metadata.get("artist") or "").strip()
        artist_hint = str(metadata.get("artist_hint") or "").strip()
        title = str(metadata.get("title") or "").strip()
        if not title or not (tag or known_artist or artist_hint):
            return None
        if tag and not artist_hint and known_artist.casefold() == tag.casefold():
            artist_hint = self._infer_artist_for_tag(
                tag, str(metadata.get("library_name") or "")
            )
            metadata["artist_hint"] = artist_hint

        # Embedded artist metadata is normally a much better query than an
        # abbreviated filename tag such as OR or 21P. Retry with the tag only
        # when the stronger query did not produce a verified result.
        query_artists = []
        preferred_artist = (
            known_artist if known_artist.casefold() != tag.casefold()
            else artist_hint or known_artist
        )
        for artist in (preferred_artist, known_artist, artist_hint, tag):
            if artist and self._normalized_lyrics_identity(artist) not in {
                self._normalized_lyrics_identity(item) for item in query_artists
            }:
                query_artists.append(artist)

        candidates = {}
        search_failed = False
        for query_title in self._lyrics_title_variants(title):
            for query_artist in query_artists:
                try:
                    payload = self._search_genius(f"{query_title} {query_artist}")
                except Exception:
                    search_failed = True
                    break
                for hit in self._genius_song_hits(payload):
                    result = hit.get("result", {}) if isinstance(hit, dict) else {}
                    page_url = str(result.get("url") or "").strip()
                    artist = str(result.get("primary_artist", {}).get("name", ""))
                    candidate_title = str(
                        result.get("title") or result.get("full_title") or ""
                    )
                    title_score = self._genius_title_score(candidate_title, title)
                    artist_exact = (
                        self._normalized_lyrics_identity(artist)
                        == self._normalized_lyrics_identity(known_artist)
                    ) if known_artist else False
                    artist_hint_match = bool(artist_hint) and (
                        not known_artist or known_artist.casefold() == tag.casefold()
                    ) and (
                        self._normalized_lyrics_identity(artist)
                        == self._normalized_lyrics_identity(artist_hint)
                    )
                    artist_tag_match = bool(tag) and self._artist_matches_tag(artist, tag)
                    if not page_url or not title_score or not (
                        artist_exact or artist_hint_match or artist_tag_match
                    ):
                        continue
                    score = title_score + (
                        50 if artist_exact or artist_hint_match else 35
                    )
                    previous = candidates.get(page_url)
                    if previous is None or score > previous[0]:
                        candidates[page_url] = (score, result)
                if candidates:
                    break
            if candidates or search_failed:
                break

        for page_url, (_score, _result) in sorted(
            candidates.items(), key=lambda item: item[1][0], reverse=True
        ):
            try:
                parser = _GeniusLyricsParser()
                parser.feed(self._fetch_text(page_url))
            except Exception:
                continue
            lyrics = self._clean_lyrics_text(parser.text())
            if lyrics:
                return {"text": lyrics, "source": "Genius", "url": page_url}
        return None

    def _fetch_preferred_lyrics(self, metadata):
        # Genius is preferred for a verified artist-tag match, but it may
        # occasionally reject automated requests. Keep LRCLIB as a reliable
        # fallback instead of turning a Genius outage into "unavailable".
        try:
            result = self._fetch_genius_lyrics(metadata)
        except Exception:
            result = None
        if result:
            return result
        if metadata.get("lyrics"):
            return {
                "text": metadata["lyrics"],
                "source": "MP3 tags",
                "url": "",
            }
        return self._fetch_lrclib_lyrics(metadata)

    def _fetch_lrclib_lyrics(self, metadata):
        artist = metadata.get("artist", "").strip()
        hint = str(metadata.get("artist_hint") or "").strip()
        if hint and (
            not artist
            or artist.casefold()
            == str(metadata.get("artist_tag") or "").casefold()
        ):
            artist = hint
        title = metadata.get("title", "").strip()
        if not title:
            return None
        duration_ms = self._cached_duration_ms(
            metadata.get("library_name", self.lyrics_song)
        )
        result = None
        last_error = None
        params = {"track_name": title}
        if artist:
            params["artist_name"] = artist
        album = metadata.get("album", "").strip()
        if album:
            params["album_name"] = album
        if duration_ms > 0:
            params["duration"] = str(round(duration_ms / 1000))
        if artist:
            exact_url = f"{LYRICS_API_URL}/get?{urllib.parse.urlencode(params)}"
            try:
                result = self._fetch_json(exact_url)
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    last_error = exc
            except Exception as exc:
                last_error = exc
        if result is None:
            results = []
            artist_tag = metadata.get("artist_tag") or artist

            def artist_matches(item):
                found_artist = item.get("artistName", "")
                return bool(artist) and (
                    self._normalized_lyrics_identity(found_artist)
                    == self._normalized_lyrics_identity(artist)
                    or bool(artist_tag)
                    and self._artist_matches_tag(found_artist, artist_tag)
                )

            searches = [{"track_name": title}]
            if artist:
                searches.insert(
                    0, {"track_name": title, "artist_name": artist}
                )
            for search in searches:
                search_params = urllib.parse.urlencode(search)
                try:
                    found = self._fetch_json(
                        f"{LYRICS_API_URL}/search?{search_params}"
                    )
                except Exception as exc:
                    last_error = exc
                    continue
                if isinstance(found, list) and found:
                    results.extend(item for item in found if isinstance(item, dict))
                    if not artist or any(
                        self._genius_title_score(item.get("trackName", ""), title)
                        and artist_matches(item)
                        for item in results
                    ):
                        break
            if not results:
                if last_error:
                    raise last_error
                return None
            candidates = [
                item for item in results
                if isinstance(item, dict)
                and self._genius_title_score(item.get("trackName", ""), title)
            ]
            if not candidates:
                return None
            matched_artist = [item for item in candidates if artist_matches(item)]
            if artist and matched_artist:
                candidates = matched_artist
            elif artist:
                # Some user tags (for example 21P) are aliases rather than
                # initials. A close duration can still disambiguate the song.
                if duration_ms <= 0:
                    return None
                target_seconds = duration_ms / 1000
                candidates = [
                    item for item in candidates
                    if abs(
                        float(item.get("duration", -1000) or -1000)
                        - target_seconds
                    ) <= 5
                ]
                if not candidates:
                    return None
            if duration_ms > 0:
                target_seconds = duration_ms / 1000
                candidates.sort(
                    key=lambda item: abs(
                        float(item.get("duration", target_seconds) or target_seconds)
                        - target_seconds
                    )
                )
            result = candidates[0]
        if not isinstance(result, dict):
            return None
        if result.get("instrumental"):
            return {
                "text": "[Instrumental]",
                "source": "LRCLIB",
                "url": "https://lrclib.net",
            }
        lyrics = self._clean_lyrics_text(
            result.get("plainLyrics") or result.get("syncedLyrics") or ""
        )
        if not lyrics:
            return None
        return {"text": lyrics, "source": "LRCLIB", "url": "https://lrclib.net"}

    def request_lyrics(self, name):
        """Load lyrics for a local song asynchronously, preferring MP3 tags."""
        if not name or name not in self._all_songs_set:
            self._lyrics_request_id += 1
            self.lyrics_song = ""
            self.lyrics_status = ""
            self.lyrics_text = ""
            self.lyrics_source = ""
            self.lyrics_source_url = ""
            self._lyrics_loading = False
            return
        if name == self.lyrics_song and (self._lyrics_loading or self.lyrics_text):
            return
        self._lyrics_request_id += 1
        request_id = self._lyrics_request_id
        self.lyrics_song = name
        self.lyrics_status = "Finding lyrics…"
        self.lyrics_text = ""
        self.lyrics_source = ""
        self.lyrics_source_url = ""
        self._lyrics_loading = True

        cached = self._lyrics_memory_cache.get(name)
        entry = self.meta.get(name, {})
        if not cached and isinstance(entry, dict):
            saved = entry.get("lyrics_cache")
            if (
                isinstance(saved, dict)
                and saved.get("text")
                and saved.get("cache_version") == LYRICS_CACHE_VERSION
            ):
                cached = saved
        if cached:
            self._finish_lyrics_request(request_id, name, cached)
            return

        def worker():
            result = None
            error = ""
            try:
                metadata = self._read_audio_lyrics_metadata(name)
                metadata["library_name"] = name
                result = self._fetch_preferred_lyrics(metadata)
            except Exception as exc:
                error = str(exc)
            self._finish_lyrics_request(request_id, name, result, error)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_lyrics_request(self, request_id, name, result, error=""):
        if request_id != self._lyrics_request_id or name != self.lyrics_song:
            return
        self._lyrics_loading = False
        if result and result.get("text"):
            result = {
                "text": _censor_lyrics_text(
                    self._clean_lyrics_text(result.get("text"))
                ),
                "source": str(result.get("source") or "Lyrics"),
                "url": str(result.get("url") or ""),
                "cache_version": LYRICS_CACHE_VERSION,
            }
            self._lyrics_memory_cache[name] = result
            self.lyrics_text = result["text"]
            self.lyrics_source = result["source"]
            self.lyrics_source_url = result["url"]
            self.lyrics_status = ""
            entry = self._meta_dict(name)
            entry["lyrics_cache"] = result
            self._meta_dirty = True
        else:
            self.lyrics_status = (
                "Lyrics not found for this song. Check its title and artist tags."
            )
            if error:
                if isinstance(error, urllib.error.HTTPError):
                    self.lyrics_status = (
                        f"Lyrics provider returned HTTP {error.code}. "
                        "Try this song again later."
                    )
                elif isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)):
                    self.lyrics_status = (
                        "Could not connect to the lyrics provider. "
                        "Check your connection and try again."
                    )
                else:
                    self.lyrics_status = "Lyrics lookup failed. Try this song again."
        self._wake_ui()

    @staticmethod
    def _volume_multiplier_from_peak(max_db):
        try:
            max_db = float(max_db)
        except (TypeError, ValueError):
            return 1.0
        if max_db <= VOLUME_TARGET_PEAK_DB:
            return 1.0
        multiplier = 10 ** ((VOLUME_TARGET_PEAK_DB - max_db) / 20.0)
        return max(0.05, min(1.0, round(multiplier, 6)))

    def _measure_audio_peak_db(self, path):
        if not shutil.which("ffmpeg"):
            return None
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-i",
            path,
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception:
            return None
        output = f"{proc.stdout}\n{proc.stderr}"
        match = re.search(r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", output)
        if not match:
            return None
        value = match.group(1).lower()
        if value == "-inf":
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _stored_volume_multiplier(self, name):
        entry = self.meta.get(name, {})
        if not isinstance(entry, dict):
            return None
        try:
            multiplier = float(entry.get("volume_multiplier", 0) or 0)
            mtime_ns = int(entry.get("volume_multiplier_mtime_ns", 0) or 0)
        except (TypeError, ValueError):
            return None
        if (
            entry.get("volume_multiplier_version") == VOLUME_MULTIPLIER_VERSION
            and mtime_ns == self._song_mtime_ns(name)
            and 0.0 < multiplier <= 1.0
        ):
            return multiplier
        return None

    def _set_volume_multiplier(self, name, multiplier, max_db=None):
        entry = self._meta_dict(name)
        entry["volume_multiplier"] = float(multiplier)
        entry["volume_multiplier_mtime_ns"] = self._song_mtime_ns(name)
        entry["volume_multiplier_version"] = VOLUME_MULTIPLIER_VERSION
        if max_db is not None:
            entry["volume_max_db"] = float(max_db)
        self.meta[name] = entry
        self._meta_dirty = True

    def _compute_volume_multiplier(self, name):
        path = self._song_path(name)
        max_db = self._measure_audio_peak_db(path)
        multiplier = self._volume_multiplier_from_peak(max_db)
        self._set_volume_multiplier(name, multiplier, max_db)
        return multiplier

    def _cached_volume_multiplier(self, name):
        multiplier = self._stored_volume_multiplier(name)
        if multiplier is not None:
            return multiplier
        return 1.0

    def _queue_volume_multiplier_compute(self, name):
        if (
            not name
            or name not in self._all_songs_set
            or self._stored_volume_multiplier(name) is not None
            or not shutil.which("ffmpeg")
        ):
            return
        with self._volume_multiplier_lock:
            if name in self._volume_multiplier_pending:
                return
            self._volume_multiplier_pending.add(name)

        def worker():
            try:
                # Serialize the expensive ffmpeg work without holding the
                # short-lived queue lock. A track change can then enqueue its
                # own analysis immediately instead of waiting for ffmpeg.
                with self._volume_analysis_lock:
                    if self._stored_volume_multiplier(name) is None:
                        self._compute_volume_multiplier(name)
                if name == self.current:
                    self._current_volume_multiplier = self._cached_volume_multiplier(name)
                    self._apply_volume(force=True)
                self._needs_redraw_hdr = True
                self.dirty = True
            except Exception:
                pass
            finally:
                with self._volume_multiplier_lock:
                    self._volume_multiplier_pending.discard(name)

        threading.Thread(target=worker, daemon=True).start()

    def _song_mtime_ns(self, name):
        try:
            return os.stat(self._song_path(name)).st_mtime_ns
        except OSError:
            return 0

    def _meta_dict(self, name):
        e = self.meta.get(name, {})
        if isinstance(e, dict):
            # A missing key produces a fresh dict; attach it before returning
            # so callers adding cached fields do not mutate a throwaway value.
            if name not in self.meta:
                self.meta[name] = e
            return e
        if isinstance(e, int):
            e = {"plays": e}
        else:
            e = {}
        self.meta[name] = e
        return e

    def _cached_duration_ms(self, name):
        if name in self._duration_ms_cache:
            return self._duration_ms_cache[name]
        e = self.meta.get(name, {})
        mtime_ns = self._song_mtime_ns(name)
        if isinstance(e, dict):
            try:
                cached_seconds = float(e.get("duration_seconds", 0) or 0)
                cached_ms = int(cached_seconds * 1000)
                if cached_ms <= 0:
                    cached_ms = int(e.get("duration_ms", 0) or 0)
                cached_mtime = int(e.get("duration_mtime_ns", 0) or 0)
            except (TypeError, ValueError):
                cached_ms = 0
                cached_mtime = 0
            if cached_ms > 0 and cached_mtime == mtime_ns:
                self._duration_ms_cache[name] = cached_ms
                return cached_ms
        self._queue_duration_load(name)
        return 0

    def _set_duration_cache(self, name, duration_ms):
        duration_ms = int(duration_ms)
        self._duration_ms_cache[name] = duration_ms
        with self._duration_load_lock:
            self._duration_ui_updates.add(name)
        self._total_listen_hours_cache.pop(name, None)
        # Keep the probe result across launches.  _cached_duration_ms validates
        # this against the MP3's mtime, so only new or changed files are read
        # again instead of rescanning the entire library at every startup.
        if duration_ms > 0:
            entry = self._meta_dict(name)
            mtime_ns = self._song_mtime_ns(name)
            try:
                stored_duration = int(entry.get("duration_ms", 0) or 0)
                stored_mtime = int(entry.get("duration_mtime_ns", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                stored_duration = 0
                stored_mtime = 0
            if (
                stored_duration != duration_ms
                or stored_mtime != mtime_ns
            ):
                entry["duration_ms"] = duration_ms
                entry["duration_seconds"] = duration_ms / 1000.0
                entry["duration_mtime_ns"] = mtime_ns
                self._meta_dirty = True
        if name == self.current and duration_ms > 0:
            self._song_len_ms = duration_ms
            self._needs_redraw_bar = True
            if self.stdscr is not None:
                self.dirty = True

    def _take_duration_ui_updates(self):
        """Return newly measured songs without polling or losing updates."""
        with self._duration_load_lock:
            updates = self._duration_ui_updates
            self._duration_ui_updates = set()
        return updates

    def _load_duration_ms(self, name):
        duration_ms = self._audio_duration_ms(self._song_path(name))
        self._set_duration_cache(name, duration_ms)
        return duration_ms

    def _queue_duration_load(self, name):
        if name in self._duration_ms_cache:
            return
        with self._duration_load_lock:
            if name in self._duration_load_pending:
                return
            self._duration_load_pending.add(name)
            self._duration_load_queue.append(name)
            self._duration_load_wakeup.set()

    def _start_duration_loader(self):
        def worker():
            while self.running:
                name = None
                with self._duration_load_lock:
                    if self._duration_load_queue:
                        name = self._duration_load_queue.popleft()
                    else:
                        self._duration_load_wakeup.clear()
                if name is None:
                    # New work sets the event in _queue_duration_load. Keep a
                    # long timeout only as a shutdown/fault-tolerance check.
                    self._duration_load_wakeup.wait(timeout=60.0)
                    continue
                self._load_duration_ms(name)
                with self._duration_load_lock:
                    self._duration_load_pending.discard(name)
                self._total_listen_hours_cache.pop(name, None)
                self._total_listen_hours(name)
                if self.sort_mode in ("duration", "listen"):
                    self._rebuild()
                    self._rebuild_play_pool()
                else:
                    self._needs_redraw_hdr = True
                    self._needs_redraw_lst = True
                    # Textual applies these cells in one small batch on its
                    # next one-second tick. Rebuilding a large table after
                    # every ffprobe result can stall playback progress for
                    # several seconds. The legacy curses UI still redraws.
                    if self.stdscr is not None:
                        self.dirty = True

        threading.Thread(target=worker, daemon=True).start()

    def _total_listen_hours(self, name):
        plays = self._plays_cache.get(name, 0)
        if name in self._total_listen_hours_cache:
            cached_plays, cached_hours = self._total_listen_hours_cache[name]
            if cached_plays == plays:
                return cached_hours
        e = self.meta.get(name, {})
        if isinstance(e, dict):
            try:
                cached_hours = float(e.get("total_listen_hours", 0) or 0)
                cached_plays = int(e.get("total_listen_plays", -1))
                cached_mtime = int(e.get("duration_mtime_ns", 0) or 0)
            except (TypeError, ValueError):
                cached_hours = 0.0
                cached_plays = -1
                cached_mtime = 0
            if (
                cached_hours >= 0
                and cached_plays == plays
                and cached_mtime == self._song_mtime_ns(name)
            ):
                self._total_listen_hours_cache[name] = (plays, cached_hours)
                return cached_hours
            try:
                cached_ms = int(e.get("total_listen_ms", 0) or 0)
                cached_plays = int(e.get("total_listen_plays", -1))
                cached_mtime = int(e.get("duration_mtime_ns", 0) or 0)
            except (TypeError, ValueError):
                cached_ms = 0
                cached_plays = -1
                cached_mtime = 0
            if (
                cached_ms >= 0
                and cached_plays == plays
                and cached_mtime == self._song_mtime_ns(name)
            ):
                total_hours = round(cached_ms / 3600000, 2)
                self._total_listen_hours_cache[name] = (plays, total_hours)
                return total_hours
        duration_ms = self._cached_duration_ms(name)
        if duration_ms <= 0:
            self._total_listen_hours_cache[name] = (plays, 0.0)
            return 0.0
        total_hours = round((plays * duration_ms) / 3600000, 2)
        self._total_listen_hours_cache[name] = (plays, total_hours)
        return total_hours

    def _inc_plays(self, name, n=1):
        now = int(time.time())
        e = self.meta.get(name, {})
        if isinstance(e, dict):
            for k in ("play_count", "plays", "count"):
                if k in e:
                    e[k] = self._extract_count(e) + n
                    e["last_played_at"] = now
                    self.meta[name] = e
                    self._plays_cache[name] = self._plays_cache.get(name, 0) + n
                    self._total_listen_hours_cache.pop(name, None)
                    self._meta_dirty = True
                    return
            e["play_count"] = n
            e["last_played_at"] = now
            self.meta[name] = e
        elif isinstance(e, int):
            self.meta[name] = {"play_count": e + n, "last_played_at": now}
        else:
            self.meta[name] = {"play_count": n, "last_played_at": now}
        self._plays_cache[name] = self._plays_cache.get(name, 0) + n
        self._total_listen_hours_cache.pop(name, None)
        self._meta_dirty = True

    def _new_playback_session(self):
        self._playback_id += 1
        return self._playback_id

    def _suppress_completion_events(self, seconds=1.0):
        self._completion_suppressed_until = max(
            self._completion_suppressed_until, time.monotonic() + seconds
        )

    def _queue_play_inc(self, name, playback_id, n=1):
        if (
            not name
            or name in ("None", "Loading...")
            or self._is_youtube_preview_name(name)
            or playback_id != self._playback_id
            or name != self.current
            or time.monotonic() < self._completion_suppressed_until
        ):
            return
        with self._play_inc_lock:
            self._pending_play_incs[name] = self._pending_play_incs.get(name, 0) + n
        self.dirty = True

    def _clear_pending_play_incs(self):
        with self._play_inc_lock:
            self._pending_play_incs = {}

    def _toggle_song_switch_lock(self):
        self.song_switch_locked = not self.song_switch_locked
        self.play_mode = "loop"
        self._pending_shuffle_next = None
        if self.song_switch_locked and self.current in self._all_songs_set:
            self._restart_current_audio()
        self.status_msg = (
            "MP3 switch lock enabled"
            if self.song_switch_locked
            else "MP3 switch lock disabled"
        )
        self._needs_redraw_hdr = True
        self.dirty = True

    def _watch_playback(self):
        while self.running:
            time.sleep(PLAYBACK_POLL_S)
            if not self.running or self._shutting_down:
                break
            try:
                current = self.current
                playback_id = self._playback_id
                if time.monotonic() < self._completion_suppressed_until:
                    continue
                if current in ("None", "Loading...") or self.paused:
                    continue
                if self._is_youtube_preview_current():
                    if not pygame.mixer.music.get_busy():
                        self._pending_preview_cleanup = True
                        self.dirty = True
                        self._wake_ui()
                    continue
                if self._song_len_ms <= 0:
                    continue
                if self.play_mode == "shuffle":
                    if not pygame.mixer.music.get_busy():
                        if self._pending_shuffle_next is None and self.play_pool:
                            self._queue_play_inc(current, playback_id, 1)
                            self._pending_shuffle_next = (
                                self._weighted_shuffle_choice(self.play_pool)
                            )
                            self._wake_ui()
                    continue
                if not pygame.mixer.music.get_busy():
                    if self._natural_loop_restart:
                        continue
                    self._queue_play_inc(current, playback_id, 1)
                    self._natural_loop_restart = True
                    self._needs_redraw_bar = True
                    self.dirty = True
                    self._wake_ui()
                    continue
            except Exception:
                pass

    def _elapsed_ms(self):
        if self._play_start == 0:
            return 0
        base = self._pause_start if self.paused else time.monotonic()
        e = int((base - self._play_start - self._pause_offset) * 1000)
        return e if e > 0 else 0

    def _current_pos_in_song(self):
        if self.current in ("None", "Loading...") or self._song_len_ms <= 0:
            return 0, 0
        if self._is_youtube_preview_current():
            return min(self._elapsed_ms(), self._song_len_ms), self._song_len_ms
        if not self.paused:
            try:
                if not pygame.mixer.music.get_busy():
                    return self._song_len_ms, self._song_len_ms
            except Exception:
                pass
        if self.play_mode != "loop":
            return min(self._elapsed_ms(), self._song_len_ms), self._song_len_ms
        return min(self._elapsed_ms(), self._song_len_ms), self._song_len_ms

    def _setup_curses(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_RED, -1)
        self.stdscr.nodelay(True)
        # Let curses translate arrow, Home/End, Delete, and Backspace escape
        # sequences into their KEY_* values before the text editor sees them.
        self.stdscr.keypad(True)
        self._is_active = False
        self.stdscr.timeout(UI_REFRESH_MS_IDLE)
        curses.curs_set(0)
        curses.mousemask(0)
        try:
            curses.init_pair(3, curses.COLOR_BLACK, -1)
        except curses.error:
            pass
        # Button-motion tracking makes the scrollbar genuinely draggable.
        sys.stdout.write("\033[?1002h\033[?1006h\033[?2004h")
        sys.stdout.flush()

    def _update_timeout(self):
        if self.stdscr is None:
            return
        if (
            self.focused == "search"
            or self.dialog_mode is not None
            or self.rename_active
            or self.download_rename_path
            or self.mp3_rename_old
            or self.delete_confirm_songs
            or self._youtube_search_debounce_at
            or self._youtube_stats_loading
        ):
            timeout = UI_REFRESH_MS_INPUT
        else:
            active = self.current not in ("None", "Loading...") and not self.paused
            timeout = UI_REFRESH_MS_ACTIVE if active else UI_REFRESH_MS_IDLE
        if timeout != self._is_active:
            self._is_active = timeout
            self.stdscr.timeout(timeout)

    def _layout(self):
        # Textual owns layout when there is no legacy curses screen. Several
        # engine transitions call this method to support the original UI.
        if self.stdscr is None:
            return
        h, w = self.stdscr.getmaxyx()
        if h < 15 or w < 20:
            return
        hdr = 7
        if self.apple_radio_enabled or self.youtube_preview_enabled:
            tab_h = 0
        else:
            tab_h = 3
        inp = 3
        self._playlist_sidebar = not (
            self.apple_radio_enabled or self.youtube_preview_enabled
        ) and w >= 64
        # In the side-navigation layout, reserve a clean row below playback
        # progress so it does not sit directly on the library panels.
        bar = 0 if self.apple_radio_enabled else (2 if self._playlist_sidebar else 1)
        content_y = hdr + bar
        if self._playlist_sidebar:
            tab_h = 0
            lst = max(1, h - content_y - inp)
            sidebar_w = max(18, min(28, w // 4))
        else:
            lst = max(1, h - hdr - tab_h - bar - inp)
        try:
            self.win_hdr = self.stdscr.subwin(hdr, w, 0, 0)
            self.win_bar = self.stdscr.subwin(max(1, bar), w, hdr, 0)
            if self._playlist_sidebar:
                self.win_tabs = self.stdscr.subwin(lst, sidebar_w, content_y, 0)
                self.win_lst = self.stdscr.subwin(
                    lst, w - sidebar_w + 1, content_y, sidebar_w - 1
                )
            else:
                self.win_tabs = self.stdscr.subwin(max(1, tab_h), w, content_y, 0)
                self.win_lst = self.stdscr.subwin(lst, w, content_y + tab_h, 0)
            self.win_inp = self.stdscr.subwin(inp, w, h - inp, 0)
        except curses.error:
            pass
        self._mark_all_dirty()

    def _mark_all_dirty(self):
        self.dirty = True
        self._needs_redraw_hdr = True
        self._needs_redraw_tabs = True
        self._needs_redraw_bar = True
        self._needs_redraw_lst = True
        self._needs_redraw_inp = True
        self._needs_redraw_ctx = True
        self._needs_redraw_confirm = True

    def _rebuild(self):
        self.all_songs = self._scan_song_names()
        self._refresh_song_lower_cache()
        self._apply_search_filter()

    def _refresh_library(self):
        """Rescan MP3 files after changes made outside the application."""
        before = set(self.all_songs)
        current = self.current
        self._rebuild()
        after = set(self.all_songs)
        added = len(after - before)
        removed = len(before - after)
        if current in before and current not in after:
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
            self.current = "None"
            self.paused = False
            self._song_len_ms = 0
            self._current_playback_path = ""
        self._use_viewed_playlist_for_playback()
        if added or removed:
            parts = []
            if added:
                parts.append(f"{added} added")
            if removed:
                parts.append(f"{removed} removed")
            self.status_msg = "Library refreshed: " + ", ".join(parts)
        else:
            self.status_msg = "Library refreshed: no changes"
        self._emit_notification(self.status_msg)
        self._mark_all_dirty()
        self._save_session_if_due(force=True)
        return added, removed

    def _apply_search_filter(self):
        if self.youtube_preview_enabled:
            self.songs = []
            self.col_width = 1
            self.selected_songs.clear()
            self._selection_anchor = None
            self._clamp_scroll()
            self._schedule_youtube_result_search()
            self.dirty = True
            self._needs_redraw_hdr = True
            self._needs_redraw_lst = True
            return
        tab_name = self._current_tab_name()
        if tab_name == "All":
            pool = list(self.all_songs)
        else:
            pool = self._playlist_song_names(tab_name)
        if self.search:
            sl = self.search.lower()
            lower_cache = self._song_lower_cache
            pool = [f for f in pool if sl in lower_cache.get(f, f.lower())]
        plays = self._plays_cache
        self._sort_songs(pool)
        self.songs = pool
        mx = max((plays.get(s, 0) for s in pool), default=0)
        self.col_width = len(str(mx)) if mx > 0 else 1
        existing = self._all_songs_set
        self.selected_songs.intersection_update(existing)
        if self._selection_anchor not in existing:
            self._selection_anchor = None
        self._clamp_scroll()
        self.dirty = True
        self._needs_redraw_hdr = True
        self._needs_redraw_lst = True

    def _search_changed(self):
        if self.youtube_preview_enabled:
            self.songs = []
            self.scroll = 0
            self._schedule_youtube_result_search()
            self._needs_redraw_hdr = True
            self._needs_redraw_lst = True
        else:
            self._apply_search_filter()
        self._needs_redraw_inp = True
        self.dirty = True
        self._update_timeout()

    def _unfocus_search(self):
        if self.focused != "search":
            return False
        self.focused = "list"
        self._needs_redraw_inp = True
        self._needs_redraw_lst = True
        self.dirty = True
        self._update_timeout()
        return True

    def _select_song_number(self, song_name, idx, shift=False):
        if shift and self._selection_anchor in self.songs:
            start = self.songs.index(self._selection_anchor)
            lo, hi = sorted((start, idx))
            self.selected_songs.update(self.songs[lo : hi + 1])
        else:
            if self.selected_songs == {song_name}:
                self.selected_songs.clear()
                self._selection_anchor = None
            else:
                self.selected_songs = {song_name}
                self._selection_anchor = song_name
        self._needs_redraw_lst = True
        self.dirty = True

    def _clear_selection_after_action(self):
        self.selected_songs.clear()
        self._selection_anchor = None
        self._needs_redraw_hdr = True
        self._needs_redraw_lst = True
        self.dirty = True

    def _clear_selection(self):
        if not self.selected_songs:
            return False
        self.selected_songs.clear()
        self._selection_anchor = None
        self._needs_redraw_lst = True
        self.dirty = True
        return True

    def _open_ctx_menu_for_song(self, song_name, screen_y, screen_x):
        if song_name in self.selected_songs:
            songs = [s for s in self.songs if s in self.selected_songs]
        else:
            songs = [song_name]
        self.ctx_menu_song = song_name
        self.ctx_menu_songs = songs
        self.ctx_menu_type = "song"
        self.ctx_menu_y = screen_y
        self.ctx_menu_x = screen_x
        self.ctx_menu_sel = 0
        self.ctx_menu_scroll = 0
        items = []
        count = len(songs)
        for pl_name in sorted(self.playlists.keys(), key=str.lower):
            if self._playlist_tag(pl_name):
                continue
            pl_set = set(self._playlist_song_names(pl_name))
            in_count = sum(1 for song in songs if song in pl_set)
            if in_count == count:
                label = f" ✕ Remove {count} from '{pl_name}'"
                if count == 1:
                    label = f" ✕ Remove from '{pl_name}'"
                items.append(("remove", pl_name, label))
            else:
                add_count = count - in_count
                label = f" + Add {add_count} to '{pl_name}'"
                if count == 1:
                    label = f" + Add to '{pl_name}'"
                items.append(("add", pl_name, label))
        items.append(("separator", None, ""))
        if count == 1:
            items.append(("rename_mp3", None, " ✎ Rename MP3"))
            items.append(("delete_mp3", None, " ✕ Delete MP3"))
        else:
            items.append(("delete_mp3", None, f" ✕ Delete {count} MP3s"))
        items.append(("separator", None, ""))
        label = " ✚ New playlist…"
        if count > 1:
            label = f" ✚ New playlist with {count} songs"
        items.append(("new_playlist", None, label))
        self.ctx_menu_items = items
        self.ctx_menu_open = True
        self._needs_redraw_ctx = True
        self._mark_all_dirty()

    def _open_ctx_menu_for_tab(self, tab_name, screen_y, screen_x):
        self.ctx_menu_song = None
        self.ctx_menu_type = "tab"
        self.ctx_menu_y = screen_y
        self.ctx_menu_x = screen_x
        self.ctx_menu_sel = 0
        self.ctx_menu_scroll = 0
        self.ctx_menu_items = [
            ("rename_tab", tab_name, f" ✎ Rename '{tab_name}'"),
            ("edit_tag", tab_name, f" # Edit artist tag"),
            ("delete_tab", tab_name, f" ✕ Delete '{tab_name}'"),
        ]
        self.ctx_menu_open = True
        self._needs_redraw_ctx = True
        self._mark_all_dirty()

    def _close_ctx_menu(self):
        self.ctx_menu_open = False
        self.ctx_menu_type = None
        self.ctx_menu_items = []
        self.ctx_menu_song = None
        self.ctx_menu_songs = []
        self._needs_redraw_ctx = True
        self._mark_all_dirty()

    def _ctx_menu_content_width(self):
        if not self.ctx_menu_items:
            return 20
        return max(len(it[2]) for it in self.ctx_menu_items if it[0] != "separator")

    def _ctx_menu_visible_count(self):
        try:
            h = self.stdscr.getmaxyx()[0]
        except Exception:
            h = 24
        return min(len(self.ctx_menu_items), max(3, h - 4))

    def _ctx_menu_execute(self):
        if not self.ctx_menu_items:
            self._close_ctx_menu()
            return
        idx = self.ctx_menu_scroll + self.ctx_menu_sel
        if idx >= len(self.ctx_menu_items):
            self._close_ctx_menu()
            return
        action, pl_name, _ = self.ctx_menu_items[idx]
        song = self.ctx_menu_song
        songs = self.ctx_menu_songs or ([song] if song else [])
        if action == "separator":
            return
        elif action == "add" and pl_name and songs:
            self._add_songs_to_playlist(pl_name, songs)
            self._clear_selection_after_action()
            self._close_ctx_menu()
        elif action == "remove" and pl_name and songs:
            self._remove_songs_from_playlist(pl_name, songs)
            self._clear_selection_after_action()
            self._close_ctx_menu()
        elif action == "new_playlist":
            self._close_ctx_menu()
            self.dialog_mode = "new_playlist_for_song"
            self.dialog_buf = ""
            self.dialog_cursor = 0
            self.dialog_context = list(songs)
            self._clear_selection_after_action()
            self._needs_redraw_inp = True
            self.dirty = True
        elif action == "delete_mp3" and songs:
            self._close_ctx_menu()
            self._start_delete_confirm(songs)
        elif action == "rename_mp3" and len(songs) == 1:
            self._close_ctx_menu()
            self._clear_selection_after_action()
            self._start_mp3_rename(songs[0])
        elif action == "rename_tab" and pl_name:
            self._close_ctx_menu()
            self._start_rename(pl_name)
        elif action == "edit_tag" and pl_name:
            self._close_ctx_menu()
            self._start_dialog("edit_playlist_tag", pl_name)
            self.dialog_buf = self._playlist_tag(pl_name)
            self.dialog_cursor = len(self.dialog_buf)
        elif action == "delete_tab" and pl_name:
            self._close_ctx_menu()
            count = len(self._playlist_song_names(pl_name))
            msg = f"Delete playlist '{pl_name}' ({count} song{'s' if count != 1 else ''})?"
            self._show_confirm(msg, lambda: self._delete_playlist(pl_name))
        else:
            self._close_ctx_menu()

    def _ctx_skip_separator(self, direction):
        idx = self.ctx_menu_scroll + self.ctx_menu_sel
        if 0 <= idx < len(self.ctx_menu_items):
            if self.ctx_menu_items[idx][0] == "separator":
                if direction > 0:
                    vis = self._ctx_menu_visible_count()
                    if self.ctx_menu_sel < vis - 1:
                        self.ctx_menu_sel += 1
                    elif self.ctx_menu_scroll + vis < len(self.ctx_menu_items):
                        self.ctx_menu_scroll += 1
                elif direction < 0:
                    if self.ctx_menu_sel > 0:
                        self.ctx_menu_sel -= 1
                    elif self.ctx_menu_scroll > 0:
                        self.ctx_menu_scroll -= 1

    def _ctx_scroll_up(self):
        if self.ctx_menu_sel > 0:
            self.ctx_menu_sel -= 1
        elif self.ctx_menu_scroll > 0:
            self.ctx_menu_scroll -= 1
        self._ctx_skip_separator(-1)
        self._needs_redraw_ctx = True
        self._mark_all_dirty()

    def _ctx_scroll_down(self):
        vis = self._ctx_menu_visible_count()
        total = len(self.ctx_menu_items)
        if self.ctx_menu_sel < vis - 1:
            self.ctx_menu_sel += 1
        elif self.ctx_menu_scroll + vis < total:
            self.ctx_menu_scroll += 1
        self._ctx_skip_separator(1)
        self._needs_redraw_ctx = True
        self._mark_all_dirty()

    def _start_rename(self, old_name):
        self.rename_active = True
        self.rename_old_name = old_name
        self.rename_buf = old_name
        self.rename_cursor = len(old_name)
        self._layout()
        self._needs_redraw_tabs = True
        self._needs_redraw_inp = True
        self._update_timeout()
        self.dirty = True

    def _finish_rename(self):
        old = self.rename_old_name
        new = self.rename_buf.strip()
        self.rename_active = False
        self.rename_old_name = ""
        self.rename_buf = ""
        self.rename_cursor = 0
        if new and new != "All":
            self._rename_playlist(old, new)
        self._layout()
        self._needs_redraw_tabs = True
        self._needs_redraw_inp = True
        self._update_timeout()
        self.dirty = True

    def _cancel_rename(self):
        self.rename_active = False
        self.rename_old_name = ""
        self.rename_buf = ""
        self.rename_cursor = 0
        self._layout()
        self._needs_redraw_tabs = True
        self._needs_redraw_inp = True
        self._update_timeout()
        self.dirty = True

    def _start_mp3_rename(self, old_name):
        self.mp3_rename_old = old_name
        self.mp3_rename_buf = old_name
        self.mp3_rename_cursor = len(old_name)
        self.focused = "search"
        self.status_msg = "Rename MP3, then press Enter"
        self._needs_redraw_hdr = True
        self._needs_redraw_inp = True
        self.dirty = True
        self._update_timeout()

    def _finish_mp3_rename(self):
        old = self.mp3_rename_old
        new = self._clean_mp3_basename(self.mp3_rename_buf)
        if new.lower().endswith(".mp3"):
            new = self._clean_mp3_basename(os.path.splitext(new)[0])
        self.mp3_rename_old = ""
        self.mp3_rename_buf = ""
        self.mp3_rename_cursor = 0
        self.focused = "list"
        if not old or not new or new == old:
            self.status_msg = "Rename canceled"
            self._needs_redraw_hdr = True
            self._needs_redraw_inp = True
            self.dirty = True
            return

        old_path = os.path.join(self.folder, f"{old}.mp3")
        new_path = os.path.join(self.folder, f"{new}.mp3")
        try:
            if os.path.exists(new_path):
                raise FileExistsError(f"{new}.mp3 already exists")
            os.replace(old_path, new_path)
            for playlist in self.playlists.values():
                for i, song in enumerate(playlist):
                    if song == old:
                        playlist[i] = new
            if old in self.meta:
                renamed_meta = self.meta.pop(old)
                if isinstance(renamed_meta, dict):
                    # A filename rename may change the artist/title used for
                    # lookup. Carry play statistics forward, but force lyrics
                    # to be resolved again for the new name.
                    renamed_meta.pop("lyrics_cache", None)
                self.meta[new] = renamed_meta
                self._save_meta()
            self._lyrics_memory_cache.pop(old, None)
            self._lyrics_memory_cache.pop(new, None)
            if old in self._plays_cache:
                self._plays_cache[new] = self._plays_cache.pop(old)
            if old in self._duration_ms_cache:
                self._duration_ms_cache[new] = self._duration_ms_cache.pop(old)
            if old in self._total_listen_hours_cache:
                self._total_listen_hours_cache[new] = (
                    self._total_listen_hours_cache.pop(old)
                )
            self._save_playlists()
            self._rebuild_play_pool()
            if self.current == old:
                self.current = new
            if self.lyrics_song == old:
                # Reject a worker that may still be fetching under the old
                # filename. The UI will immediately request the renamed song.
                self._lyrics_request_id += 1
                self.lyrics_song = ""
                self.lyrics_status = ""
                self.lyrics_text = ""
                self.lyrics_source = ""
                self.lyrics_source_url = ""
                self._lyrics_loading = False
            if old in self.selected_songs:
                self.selected_songs.remove(old)
                self.selected_songs.add(new)
            if self._selection_anchor == old:
                self._selection_anchor = new
            self.status_msg = f"Renamed: {new}"
            self._emit_notification(self.status_msg)
        except Exception as exc:
            self.status_msg = f"Rename failed: {str(exc)[:120]}"
            self._emit_notification(self.status_msg, severity="error")
        self._rebuild()
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._needs_redraw_inp = True
        self._needs_redraw_lst = True
        self.dirty = True

    def _cancel_mp3_rename(self):
        if not self.mp3_rename_old:
            return
        self.mp3_rename_old = ""
        self.mp3_rename_buf = ""
        self.mp3_rename_cursor = 0
        self.status_msg = "Rename canceled"
        self.focused = "list"
        self._needs_redraw_hdr = True
        self._needs_redraw_inp = True
        self.dirty = True

    def _start_delete_confirm(self, songs):
        self.delete_confirm_songs = list(songs)
        self._clear_selection_after_action()
        count = len(self.delete_confirm_songs)
        if count == 1:
            msg = f"Delete MP3 '{self.delete_confirm_songs[0]}'?"
        else:
            msg = f"Delete {count} MP3s?"
        self._show_confirm(
            msg,
            self._confirm_delete_mp3,
            self._cancel_delete_confirm,
        )

    def _cancel_delete_confirm(self):
        self.delete_confirm_songs = []
        self.status_msg = "Delete canceled"
        self.focused = "list"
        self._needs_redraw_hdr = True
        self._needs_redraw_inp = True
        self.dirty = True

    @staticmethod
    def _move_to_trash(path):
        """Move a file to the OS trash instead of permanently deleting it."""
        try:
            from send2trash import send2trash
            send2trash(path)
            return
        except ImportError:
            pass
        trash = os.path.expanduser("~/.Trash")
        os.makedirs(trash, exist_ok=True)
        target = Player._unique_path(os.path.join(trash, os.path.basename(path)))
        shutil.move(path, target)

    def _confirm_delete_mp3(self):
        songs = list(self.delete_confirm_songs)
        self.delete_confirm_songs = []
        if not songs:
            return
        deleted = 0
        failed = 0
        for song in songs:
            path = os.path.join(self.folder, f"{song}.mp3")
            try:
                self._move_to_trash(path)
                deleted += 1
            except FileNotFoundError:
                deleted += 1
            except Exception:
                failed += 1
                continue
            for playlist in self.playlists.values():
                while song in playlist:
                    playlist.remove(song)
            self.selected_songs.discard(song)
            if self.current == song:
                try:
                    pygame.mixer.music.stop()
                    pygame.mixer.music.unload()
                except Exception:
                    pass
                self.current = "None"
                self._song_len_ms = 0
                self.paused = False
        if deleted:
            self._save_playlists()
        if failed:
            self.status_msg = f"Moved {deleted} to Trash, failed {failed}"
            self._emit_notification(self.status_msg, severity="warning")
        else:
            self.status_msg = f"Moved {deleted} MP3{'s' if deleted != 1 else ''} to Trash"
            self._emit_notification(self.status_msg)
        self.focused = "list"
        self._rebuild()
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._needs_redraw_inp = True
        self._needs_redraw_lst = True
        self.dirty = True

    def _start_dialog(self, mode, context=None):
        self.dialog_mode = mode
        self.dialog_buf = ""
        self.dialog_cursor = 0
        self.dialog_context = context
        self._needs_redraw_inp = True
        self.dirty = True

    def _finish_dialog(self):
        mode = self.dialog_mode
        buf = self.dialog_buf.strip()
        ctx = self.dialog_context
        self.dialog_mode = None
        self.dialog_buf = ""
        self.dialog_cursor = 0
        self.dialog_context = None
        if not buf and mode != "edit_playlist_tag":
            self._needs_redraw_inp = True
            self.dirty = True
            return
        if mode == "new_playlist":
            self._create_playlist(buf)
        elif mode == "new_playlist_for_song":
            self._create_playlist(buf)
            if ctx and buf in self.playlists:
                songs = ctx if isinstance(ctx, list) else [ctx]
                self._add_songs_to_playlist(buf, songs)
        elif mode == "edit_playlist_tag" and ctx:
            self._set_playlist_tag(ctx, buf)
        self._needs_redraw_inp = True
        self._needs_redraw_tabs = True
        self.dirty = True

    def _cancel_dialog(self):
        self.dialog_mode = None
        self.dialog_buf = ""
        self.dialog_cursor = 0
        self.dialog_context = None
        self._needs_redraw_inp = True
        self.dirty = True

    def _is_youtube_preview_current(self):
        return self._youtube_temp_path is not None and self.current not in (
            "None",
            "Loading...",
        )

    def _is_active_youtube_result(self, result):
        """Match a result to the preview even after stats enrich its mapping."""
        active = self._youtube_active_result
        if not isinstance(result, dict) or not isinstance(active, dict):
            return False
        for key in ("id", "url", "webpage_url"):
            left = str(result.get(key) or "")
            right = str(active.get(key) or "")
            if left and right:
                return left == right
        return (
            str(result.get("title") or "") == str(active.get("title") or "")
            and str(result.get("channel") or "")
            == str(active.get("channel") or "")
        )

    def _is_youtube_preview_name(self, name):
        return self._youtube_temp_path is not None and name not in (
            "None",
            "Loading...",
        )

    def _cleanup_youtube_preview(self, stop_audio=False):
        temp_dir = self._youtube_temp_dir
        was_preview = self._youtube_temp_path is not None
        if stop_audio and was_preview:
            self._suppress_completion_events()
            self._new_playback_session()
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()
            except Exception:
                pass
        self._youtube_temp_dir = None
        self._youtube_temp_path = None
        self._pending_preview_cleanup = False
        self._current_playback_path = ""
        if temp_dir:
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        if stop_audio and was_preview:
            self.current = "None"
            self._song_len_ms = 0
            self._play_start = 0.0
            self.paused = False
            self._needs_redraw_hdr = True
            self._needs_redraw_bar = True
            self.dirty = True

    def _text_field(self, field):
        if field == "search":
            return self.search, self.search_cursor, 220
        if field == "mp3_rename":
            return self.mp3_rename_buf, self.mp3_rename_cursor, 180
        if field == "download_rename":
            return self.download_rename_buf, self.download_rename_cursor, 180
        if field == "tab_rename":
            return self.rename_buf, self.rename_cursor, 40
        if field == "dialog":
            return self.dialog_buf, self.dialog_cursor, 40
        return "", 0, 0

    def _set_text_field(self, field, text, cursor):
        cursor = max(0, min(int(cursor), len(text)))
        if field == "search":
            old = self.search
            self.search = text
            self.search_cursor = cursor
            if text != old:
                self._search_changed()
            else:
                self._needs_redraw_inp = True
        elif field == "mp3_rename":
            self.mp3_rename_buf = text
            self.mp3_rename_cursor = cursor
            self._needs_redraw_inp = True
        elif field == "download_rename":
            self.download_rename_buf = text
            self.download_rename_cursor = cursor
            self._needs_redraw_inp = True
        elif field == "tab_rename":
            self.rename_buf = text
            self.rename_cursor = cursor
            # Playlist rename now lives in the shared bottom input bar.
            self._needs_redraw_inp = True
        elif field == "dialog":
            self.dialog_buf = text
            self.dialog_cursor = cursor
            self._needs_redraw_inp = True
        self.dirty = True

    def _clipboard_text(self):
        try:
            proc = subprocess.run(
                ["pbpaste"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=0.5,
                check=False,
            )
            if proc.returncode == 0:
                return proc.stdout or ""
        except Exception:
            pass
        return ""

    def _set_clipboard_text(self, text):
        try:
            subprocess.run(
                ["pbcopy"],
                input=text or "",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=0.5,
                check=False,
            )
        except Exception:
            pass

    @staticmethod
    def _clean_paste_text(text):
        text = (text or "").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        return "".join(ch for ch in text if ch == "\t" or ch == " " or ch.isprintable())

    @staticmethod
    def _prev_word_pos(text, cursor):
        i = max(0, min(cursor, len(text)))
        while i > 0 and text[i - 1].isspace():
            i -= 1
        while i > 0 and not text[i - 1].isspace():
            i -= 1
        return i

    @staticmethod
    def _next_word_pos(text, cursor):
        i = max(0, min(cursor, len(text)))
        n = len(text)
        while i < n and not text[i].isspace():
            i += 1
        while i < n and text[i].isspace():
            i += 1
        return i

    def _edit_text_field(self, field, ch):
        text, cursor, max_len = self._text_field(field)
        if max_len <= 0:
            return False

        def insert(value):
            nonlocal text, cursor
            value = self._clean_paste_text(value)
            if not value:
                return False
            room = max_len - len(text)
            if room <= 0:
                return False
            value = value[:room]
            text = text[:cursor] + value + text[cursor:]
            cursor += len(value)
            return True

        changed = False
        moved = False
        if ch in (127, 8, curses.KEY_BACKSPACE):
            if cursor > 0:
                text = text[: cursor - 1] + text[cursor:]
                cursor -= 1
                changed = True
        elif ch in (curses.KEY_DC, 4):
            if cursor < len(text):
                text = text[:cursor] + text[cursor + 1:]
                changed = True
        elif ch in (curses.KEY_LEFT, 2):
            if cursor > 0:
                cursor -= 1
                moved = True
        elif ch in (curses.KEY_RIGHT, 6):
            if cursor < len(text):
                cursor += 1
                moved = True
        elif ch in (curses.KEY_HOME, 1):
            if cursor != 0:
                cursor = 0
                moved = True
        elif ch in (curses.KEY_END, 5):
            if cursor != len(text):
                cursor = len(text)
                moved = True
        elif ch == 21:
            if cursor > 0:
                self._text_kill_ring = text[:cursor]
                text = text[cursor:]
                cursor = 0
                changed = True
        elif ch == 11:
            if cursor < len(text):
                self._text_kill_ring = text[cursor:]
                text = text[:cursor]
                changed = True
        elif ch == 23:
            start = self._prev_word_pos(text, cursor)
            if start != cursor:
                self._text_kill_ring = text[start:cursor]
                text = text[:start] + text[cursor:]
                cursor = start
                changed = True
        elif ch == 24:
            if text:
                self._text_kill_ring = text
                self._set_clipboard_text(text)
                text = ""
                cursor = 0
                changed = True
        elif ch == 25:
            changed = insert(self._text_kill_ring)
        elif ch == 22:
            changed = insert(self._clipboard_text())
        elif ch == 9 and field != "search":
            changed = insert(" ")
        elif ch == curses.KEY_BTAB:
            new_cursor = self._prev_word_pos(text, cursor)
            if new_cursor != cursor:
                cursor = new_cursor
                moved = True
        elif 32 <= ch <= 0x10FFFF:
            changed = insert(chr(ch))

        if changed or moved:
            self._set_text_field(field, text, cursor)
            return True
        return False

    def _stop_youtube_preview(self):
        if not self._youtube_temp_path:
            return False
        self._cleanup_youtube_preview(stop_audio=True)
        self.status_msg = "Deleted YouTube preview"
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self.dirty = True
        return True

    def _enter_apple_radio_mode(self):
        if self.youtube_preview_enabled or self._youtube_temp_path:
            self._exit_youtube_preview_mode()
        self.show_shuffle_weights = False
        self.apple_radio_enabled = True
        self.scroll = 0
        self.songs = []
        self.selected_songs.clear()
        self._selection_anchor = None
        self.apple_radio_now = ""
        self.apple_radio_music_now = ""
        self._apple_radio_last_poll = 0.0
        self.status_msg = "Apple Music Radio: click a station to tune in"
        self._layout()
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._needs_redraw_lst = True
        self._needs_redraw_inp = True
        self.dirty = True
        self._update_timeout()

    def _exit_apple_radio_mode(self):
        if not self.apple_radio_enabled:
            return False
        stopped = self._stop_apple_radio_audio()
        self.apple_radio_enabled = False
        self.apple_radio_active = ""
        self.apple_radio_now = ""
        self.apple_radio_music_now = ""
        can_restore_mp3 = bool(self._pre_radio_song)
        if self.current.startswith("Apple Radio:") and not can_restore_mp3:
            self.current = "None"
        if self.play_tab == "Apple Radio":
            self.play_tab = "All"
        self.status_msg = ""
        self._rebuild()
        restored_mp3 = can_restore_mp3 and self._restore_mp3_after_radio()
        if restored_mp3:
            self.status_msg = "Restored MP3 position"
        elif self.current.startswith("Apple Radio:"):
            self.current = "None"
        self._layout()
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._needs_redraw_lst = True
        self._needs_redraw_inp = True
        self.dirty = True
        self._update_timeout()
        return True

    def _toggle_apple_radio_mode(self):
        if self.apple_radio_enabled:
            self._exit_apple_radio_mode()
        else:
            self._enter_apple_radio_mode()

    @staticmethod
    def _apple_music_app_url(url):
        if url.startswith("https://music.apple.com/"):
            return "music://" + url[len("https://") :]
        return url

    def _hide_music_app(self):
        try:
            subprocess.run(
                ["osascript", "-e", 'tell application "Music" to set visible to false'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
        except Exception:
            pass

    def _stop_apple_radio_audio(self):
        if not self.apple_radio_active:
            return False
        script = 'tell application "Music" to stop'
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
                check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def _clear_radio_resume_state(self):
        self._pre_radio_song = ""
        self._pre_radio_pos_ms = 0
        self._pre_radio_paused = False
        self._pre_radio_play_tab = "All"

    def _snapshot_mp3_for_radio(self):
        if self._pre_radio_song:
            return
        if self.current not in self._all_songs_set:
            return
        pos_ms, _ = self._current_pos_in_song()
        self._pre_radio_song = self.current
        self._pre_radio_pos_ms = max(0, int(pos_ms))
        self._pre_radio_paused = bool(self.paused)
        self._pre_radio_play_tab = self.play_tab

    def _restore_mp3_after_radio(self):
        song = self._pre_radio_song
        pos_ms = self._pre_radio_pos_ms
        paused = self._pre_radio_paused
        play_tab = self._pre_radio_play_tab
        self._clear_radio_resume_state()
        if not song or song not in self._all_songs_set:
            return False
        self.play_tab = play_tab if play_tab in self.tab_names else "All"
        self._rebuild_play_pool()
        self.current = song
        self.paused = paused
        self._song_len_ms = self._cached_duration_ms(song)
        self._restart_current_audio(target_ms=pos_ms, suppress_completion=True)
        return self.current == song

    def _play_apple_radio_station(self, station):
        # Keep enough of the local playback state to resume it when the user
        # cancels radio. Switching between radio stations leaves the original
        # snapshot intact.
        self._snapshot_mp3_for_radio()
        self._suppress_completion_events()
        self._new_playback_session()
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
        self._cleanup_youtube_preview(stop_audio=True)
        self._clear_pending_play_incs()
        self._pending_shuffle_next = None
        self._song_len_ms = 0
        self._current_playback_path = ""
        self._current_volume_multiplier = 1.0
        self._play_start = 0.0
        self._pause_offset = 0.0
        self._pause_start = 0.0
        self.paused = False
        self.apple_radio_enabled = True
        self.apple_radio_active = station.get("name", "")
        self.apple_radio_now = "Waiting for Music..."
        self.apple_radio_music_now = ""
        self._apple_radio_last_poll = 0.0
        self.current = f"Apple Radio: {self.apple_radio_active}"
        self.play_tab = "Apple Radio"
        self.status_msg = ""
        url = station.get("url", "")
        opened = False
        if url:
            app_url = self._apple_music_app_url(url)
            try:
                proc = subprocess.run(
                    ["open", "-g", "-j", "-b", "com.apple.Music", app_url],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
                opened = proc.returncode == 0
            except Exception:
                opened = False
            if not opened:
                try:
                    proc = subprocess.run(
                        ["open", "-g", "-j", app_url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=1.5,
                        check=False,
                    )
                    opened = proc.returncode == 0
                except Exception:
                    opened = False
            if not opened:
                script = f'open location "{app_url}"'
                try:
                    proc = subprocess.run(
                        ["osascript", "-e", script],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=1.5,
                        check=False,
                    )
                    opened = proc.returncode == 0
                except Exception:
                    opened = False
        if not opened:
            self.status_msg = "Could not open Apple Music station"
            self.apple_radio_now = "Tune failed"
            self.apple_radio_music_now = ""
        else:
            self._hide_music_app()
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._needs_redraw_lst = True
        self.dirty = True
        self._update_timeout()

    def _poll_apple_radio_now(self):
        if not self.apple_radio_enabled or not self.apple_radio_active:
            return
        now = time.monotonic()
        if now - self._apple_radio_last_poll < APPLE_RADIO_POLL_S:
            return
        self._apple_radio_last_poll = now
        script = (
            'tell application "Music"\n'
            'try\n'
            'set streamTitle to current stream title\n'
            'if streamTitle is not equal to missing value then\n'
            'if streamTitle is not equal to "" then return streamTitle\n'
            'end if\n'
            'end try\n'
            'try\n'
            'set trackTitle to name of current track\n'
            'set trackArtist to artist of current track\n'
            'if trackTitle is not equal to missing value then\n'
            'if trackTitle is not equal to "" then\n'
            'if trackArtist is not equal to missing value then\n'
            'if trackArtist is not equal to "" then\n'
            'return trackArtist & " - " & trackTitle\n'
            'end if\n'
            'end if\n'
            'return trackTitle\n'
            'end if\n'
            'end if\n'
            'end try\n'
            'try\n'
            'return player state as string\n'
            'end try\n'
            'return ""\n'
            'end tell'
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1.5,
                check=False,
            )
        except Exception:
            return
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().lower()
            if (
                "not authorized" in err
                or "not allowed" in err
                or "-1743" in err
            ):
                current = "Allow Terminal/Codex to control Music"
            else:
                return
        else:
            current = (proc.stdout or "").strip()
        if current in ("playing", "paused", "stopped"):
            if self.apple_radio_music_now in ("", "Waiting for Music..."):
                current = "Music is playing radio"
            else:
                return
        if current and current == self.apple_radio_active:
            current = "Music is playing radio"
        if current and current != self.apple_radio_music_now:
            self.apple_radio_music_now = current
            self.apple_radio_now = current
            self._needs_redraw_hdr = True
            self._needs_redraw_lst = True
            self.dirty = True

    def _queue_apple_radio_poll(self):
        """Poll Music in the background so AppleScript never stalls the UI."""
        if (
            not self.apple_radio_enabled
            or not self.apple_radio_active
            or self._apple_radio_polling
            or time.monotonic() - self._apple_radio_last_poll < APPLE_RADIO_POLL_S
        ):
            return
        self._apple_radio_polling = True

        def worker():
            try:
                self._poll_apple_radio_now()
            finally:
                self._apple_radio_polling = False
                self._wake_ui()

        threading.Thread(target=worker, daemon=True).start()

    def _apple_radio_now_text(self):
        current = (self.apple_radio_music_now or self.apple_radio_now or "").strip()
        if not current or current == "Waiting for Music...":
            return "Waiting for Music..."
        return current

    def _exit_current_youtube_preview(self):
        if self._youtube_loading:
            self._youtube_request_id += 1
            self._youtube_loading = False
            self._terminate_proc(self._youtube_preview_proc)
            self.status_msg = "Canceled YouTube preview"
            self._needs_redraw_hdr = True
            self.dirty = True
            return True
        if self._stop_youtube_preview():
            return True
        self.status_msg = "No YouTube preview playing"
        self._needs_redraw_hdr = True
        self.dirty = True
        return False

    def _exit_youtube_preview_mode(self):
        if not self.youtube_preview_enabled and not self._youtube_temp_path:
            return False
        self._youtube_request_id += 1
        self._youtube_search_request_id += 1
        self._youtube_stats_request_id += 1
        self._youtube_loading = False
        self._youtube_results_loading = False
        self._youtube_stats_loading = False
        self._terminate_proc(self._youtube_preview_proc)
        if self._youtube_search_proc is not None:
            self._youtube_search_request_id += 1
            self._terminate_proc(self._youtube_search_proc)
        self.youtube_results = []
        self._youtube_active_result = None
        self._youtube_search_debounce_at = 0.0
        self._youtube_search_pending_query = ""
        self._youtube_results_query = ""
        self._youtube_view = "search"
        self._youtube_channel_title = ""
        self._youtube_channel_url = ""
        stopped = self._stop_youtube_preview()
        self.youtube_preview_enabled = False
        self.status_msg = "Deleted YouTube preview" if stopped else ""
        self._rebuild()
        self._layout()
        self._needs_redraw_hdr = True
        self._needs_redraw_lst = True
        self._needs_redraw_inp = True
        self.dirty = True
        return True

    def _youtube_result_limit(self):
        return max(YT_SEARCH_RESULT_LIMIT, self._vis())

    def _youtube_query_from_search(self):
        query = self.search.strip()
        if self.youtube_preview_enabled and query:
            return query
        return ""

    def _youtube_cache_key(self, query=None, channel_url=None):
        if channel_url:
            return f"channel:{channel_url}"
        return f"search:{(query or '').lower()}"

    def _reset_youtube_channel_view(self):
        if self._youtube_view == "channel":
            self._youtube_view = "search"
            self._youtube_channel_title = ""
            self._youtube_channel_url = ""
            self.scroll = 0

    def _youtube_result_kind(self, result):
        if not isinstance(result, dict):
            return "video"
        return result.get("kind") or result.get("_type") or "video"

    def _is_youtube_channel_result(self, result):
        return self._youtube_result_kind(result) == "channel"

    def _youtube_channel_target(self, result):
        if not isinstance(result, dict):
            return ""
        url = (
            result.get("channel_url")
            or result.get("url")
            or result.get("webpage_url")
            or ""
        )
        url = self._youtube_abs_url(url)
        if isinstance(url, str) and url.startswith("http"):
            return url.rstrip("/")
        channel_id = str(result.get("channel_id") or result.get("id") or "").strip()
        if channel_id.startswith("@"):
            return f"https://www.youtube.com/{channel_id}"
        if channel_id.startswith("UC"):
            return f"https://www.youtube.com/channel/{channel_id}"
        return ""

    def _youtube_channel_videos_target(self, result_or_url):
        if isinstance(result_or_url, dict):
            url = self._youtube_channel_target(result_or_url)
        else:
            url = str(result_or_url or "").strip()
        if not url:
            return ""
        url = url.rstrip("/")
        if url.endswith("/videos") or "/videos?" in url:
            return url
        return f"{url}/videos"

    @staticmethod
    def _youtube_abs_url(url):
        url = str(url or "").strip()
        if not url:
            return ""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("www.youtube.com/") or url.startswith("youtube.com/"):
            return f"https://{url}"
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"https://www.youtube.com{url}"
        if url.startswith("@"):
            return f"https://www.youtube.com/{url}"
        return url

    @staticmethod
    def _youtube_search_url(query, sp=""):
        params = {"search_query": query}
        if sp:
            params["sp"] = sp
        return f"https://www.youtube.com/results?{urllib.parse.urlencode(params)}"

    def _schedule_youtube_result_search(self, delay=0.08):
        query = self._youtube_query_from_search()
        if not query:
            self._reset_youtube_channel_view()
            self.youtube_results = []
            self._youtube_results_query = ""
            self._youtube_results_loading = False
            self._youtube_search_debounce_at = 0.0
            self._youtube_search_pending_query = ""
            return False
        if self._youtube_view != "search":
            self._reset_youtube_channel_view()
        cache_key = self._youtube_cache_key(query=query)
        limit = self._youtube_result_limit()
        if cache_key == self._youtube_results_query and (
            self._youtube_results_loading or len(self.youtube_results) >= limit
        ):
            return False
        if not self._yt_dlp_available():
            self.youtube_results = []
            self._youtube_results_loading = False
            self.status_msg = "yt-dlp is required for YouTube search"
            self._needs_redraw_hdr = True
            self._needs_redraw_lst = True
            self.dirty = True
            return False
        self._terminate_proc(self._youtube_search_proc)
        cached = self._youtube_search_cache.get(cache_key)
        if cached and len(cached) >= limit:
            self._youtube_results_query = cache_key
            self._youtube_results_loading = False
            self._youtube_search_debounce_at = 0.0
            self._youtube_search_pending_query = ""
            self.youtube_results = [dict(r) for r in cached]
            self._sort_youtube_results()
            if self._youtube_stats_missing(self.youtube_results):
                self._start_youtube_stats_enrichment(cache_key, self.youtube_results)
            self.status_msg = f"{len(self.youtube_results)} YouTube result"
            if len(self.youtube_results) != 1:
                self.status_msg += "s"
            self._needs_redraw_hdr = True
            self._needs_redraw_lst = True
            self.dirty = True
            return True
        self._youtube_search_pending_query = query
        self._youtube_search_debounce_at = time.monotonic() + delay
        self.status_msg = f"Search queued: {query}"
        self._needs_redraw_hdr = True
        self._needs_redraw_lst = True
        self.dirty = True
        self._update_timeout()
        return True

    def _youtube_video_target(self, result):
        if not isinstance(result, dict):
            return ""
        if self._is_youtube_channel_result(result):
            return ""
        url = result.get("url") or ""
        if isinstance(url, str) and url.startswith("http"):
            return url
        if isinstance(url, str) and url.startswith("ytsearch"):
            return url
        video_id = result.get("id") or ""
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        return ""

    @staticmethod
    def _youtube_video_id_from_url(url):
        try:
            parsed = urllib.parse.urlparse(str(url or ""))
            host = parsed.netloc.lower()
            path = parsed.path.strip("/")
            if host.endswith("youtu.be") and path:
                return path.split("/", 1)[0]
            if path == "watch":
                values = urllib.parse.parse_qs(parsed.query).get("v") or []
                return values[0] if values else ""
            if path.startswith("shorts/") or path.startswith("embed/"):
                return path.split("/", 1)[1].split("/", 1)[0]
        except Exception:
            pass
        return ""

    def _youtube_download_result(self):
        if isinstance(self._youtube_active_result, dict):
            return self._youtube_active_result
        for result in self.youtube_results:
            if not self._is_youtube_channel_result(result):
                return result
        return None

    def _radio_download_result(self):
        title = self._apple_radio_now_text()
        blocked = {
            "",
            "Waiting for Music...",
            "Music is playing radio",
            "Allow Terminal/Codex to control Music",
        }
        if title in blocked:
            return None
        return {"title": title, "url": f"ytsearch1:{title}"}

    @staticmethod
    def _parse_yt_count(text):
        if not isinstance(text, str):
            return None
        match = re.search(r"([\d,.]+)\s*([KMB]?)", text.replace(",", ""), re.I)
        if not match:
            return None
        try:
            value = float(match.group(1))
        except ValueError:
            return None
        mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
        return int(value * mult.get(match.group(2).upper(), 1))

    @staticmethod
    def _fmt_yt_count(value):
        if value is None:
            return "-"
        try:
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            return "-"
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.1f}B"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.1f}K"
        return str(value)

    @staticmethod
    def _coerce_yt_int(value):
        if value in (None, "", "NA", "None", "null"):
            return None
        try:
            number = float(value)
            return int(number) if math.isfinite(number) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def _sort_youtube_results(self):
        mode = self.youtube_sort_mode
        reverse = self.youtube_sort_reverse
        if mode == "views":
            key = lambda item: (
                self._is_youtube_channel_result(item),
                self._coerce_yt_int(item.get("views")) is not None,
                self._coerce_yt_int(item.get("views")) or 0,
            )
        elif mode == "likes":
            key = lambda item: (
                self._is_youtube_channel_result(item),
                self._coerce_yt_int(item.get("likes")) is not None,
                self._coerce_yt_int(item.get("likes")) or 0,
            )
        elif mode == "channel":
            key = lambda item: (
                self._is_youtube_channel_result(item),
                str(item.get("channel") or "").lower(),
                str(item.get("title") or "").lower(),
            )
        else:
            key = lambda item: (
                0 if self._is_youtube_channel_result(item) else 1,
                str(item.get("title") or "").lower(),
            )
        self.youtube_results.sort(key=key, reverse=reverse)

    @staticmethod
    def _unique_path(path):
        if not os.path.exists(path):
            return path
        root, ext = os.path.splitext(path)
        i = 2
        while True:
            candidate = f"{root} ({i}){ext}"
            if not os.path.exists(candidate):
                return candidate
            i += 1

    @staticmethod
    def _clean_mp3_basename(name):
        name = name.strip()
        if name.lower().endswith(".mp3"):
            name = name[:-4].strip()
        name = re.sub(r'[\\/:*?"<>|]+', " ", name)
        name = re.sub(r"\s+", " ", name).strip(" .")
        return name or "Untitled"

    def _start_download_rename(self, path, title):
        self.download_rename_path = path
        base = os.path.splitext(os.path.basename(path))[0]
        self.download_rename_buf = self._clean_mp3_basename(title or base)
        self.download_rename_cursor = len(self.download_rename_buf)
        self.search = ""
        self.search_cursor = 0
        self.focused = "search"
        self.status_msg = "Rename downloaded MP3, then press Enter"
        self._needs_redraw_hdr = True
        self._needs_redraw_inp = True
        self.dirty = True

    def _finish_download_rename(self):
        path = self.download_rename_path
        if not path:
            return
        name = self._clean_mp3_basename(self.download_rename_buf)
        target = self._unique_path(os.path.join(self.folder, f"{name}.mp3"))
        self.download_rename_path = ""
        self.download_rename_buf = ""
        self.download_rename_cursor = 0
        try:
            if os.path.abspath(path) != os.path.abspath(target):
                os.replace(path, target)
            self.status_msg = f"Saved: {os.path.splitext(os.path.basename(target))[0]}"
            self._emit_notification(self.status_msg)
        except Exception as exc:
            self.status_msg = f"Rename failed: {str(exc)[:120]}"
            self._emit_notification(self.status_msg, severity="error")
        self.focused = "list"
        self._rebuild()
        self._needs_redraw_hdr = True
        self._needs_redraw_inp = True
        self._needs_redraw_lst = True
        self.dirty = True

    def _cancel_download_rename(self):
        if not self.download_rename_path:
            return
        title = os.path.splitext(os.path.basename(self.download_rename_path))[0]
        self.download_rename_path = ""
        self.download_rename_buf = ""
        self.download_rename_cursor = 0
        self.status_msg = f"Saved: {title}"
        self.focused = "list"
        self._rebuild()
        self._needs_redraw_hdr = True
        self._needs_redraw_inp = True
        self._needs_redraw_lst = True
        self.dirty = True

    def _extract_youtube_search_results(self, entries, limit):
        results = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("_type") or entry.get("ie_key") or "video"
            kind = str(kind).lower()
            raw_url = self._youtube_abs_url(
                entry.get("webpage_url")
                or entry.get("webpage_url_basename")
                or entry.get("url")
                or ""
            )
            channel_url = self._youtube_abs_url(entry.get("channel_url") or "")
            raw_entry_url = str(entry.get("url") or "").strip()
            is_channel = (
                kind == "channel"
                or kind.startswith("youtubechannel")
                or (
                    isinstance(raw_url, str)
                    and (
                        "/channel/" in raw_url
                        or "/c/" in raw_url
                        or "/user/" in raw_url
                        or re.search(r"youtube\.com/@[^/?#]+/?$", raw_url)
                    )
                )
                or raw_entry_url.startswith("@")
            )
            title = str(entry.get("title") or "Untitled").strip()
            if not title:
                continue
            url = channel_url if is_channel and channel_url else raw_url
            if not (isinstance(url, str) and url.startswith("http")):
                if is_channel:
                    item_id = entry.get("channel_id") or entry.get("id") or ""
                    if str(item_id).startswith("@"):
                        url = f"https://www.youtube.com/{item_id}"
                    elif str(item_id).startswith("UC"):
                        url = f"https://www.youtube.com/channel/{item_id}"
                    elif raw_entry_url.startswith("@"):
                        url = f"https://www.youtube.com/{entry.get('url')}"
                else:
                    item_id = entry.get("id") or entry.get("display_id") or ""
                    if item_id:
                        url = f"https://www.youtube.com/watch?v={item_id}"
            item_id = (
                entry.get("id")
                or entry.get("channel_id")
                or url
                or entry.get("url")
                or title
            )
            seen_key = (("channel" if is_channel else "video"), str(item_id), str(url))
            if seen_key in seen:
                continue
            if not url:
                continue
            views = entry.get("view_count")
            likes = entry.get("like_count")
            channel = str(
                entry.get("channel")
                or entry.get("uploader")
                or entry.get("channel_title")
                or (title if is_channel else "")
            ).strip()
            seen.add(seen_key)
            results.append(
                {
                    "title": title,
                    "channel": channel,
                    "id": item_id,
                    "url": url,
                    "views": views,
                    "likes": likes,
                    "kind": "channel" if is_channel else "video",
                }
            )
            if len(results) >= limit:
                break
        return results

    def _extract_youtube_channels_from_video_entries(self, entries, limit):
        results = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = str(
                entry.get("channel")
                or entry.get("uploader")
                or entry.get("channel_title")
                or ""
            ).strip()
            raw_url = self._youtube_abs_url(entry.get("channel_url") or "")
            channel_id = str(
                entry.get("channel_id") or entry.get("uploader_id") or ""
            ).strip()
            if not title and not raw_url and not channel_id:
                continue
            if not title:
                title = channel_id or raw_url.rsplit("/", 1)[-1] or "Channel"
            url = raw_url
            if not (isinstance(url, str) and url.startswith("http")):
                if channel_id.startswith("@"):
                    url = f"https://www.youtube.com/{channel_id}"
                elif channel_id.startswith("UC"):
                    url = f"https://www.youtube.com/channel/{channel_id}"
            if not (isinstance(url, str) and url.startswith("http")):
                continue
            key = channel_id or url
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "title": title,
                    "channel": title,
                    "id": channel_id or url,
                    "url": url.rstrip("/"),
                    "views": None,
                    "likes": None,
                    "kind": "channel",
                }
            )
            if len(results) >= limit:
                break
        return results

    def _extract_youtube_channel_search_results(self, entries, limit):
        results = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = str(
                entry.get("channel")
                or entry.get("uploader")
                or entry.get("title")
                or ""
            ).strip()
            if not title:
                continue
            raw_url = self._youtube_abs_url(
                entry.get("channel_url")
                or entry.get("webpage_url")
                or entry.get("url")
                or ""
            )
            raw_entry_url = str(entry.get("url") or "").strip()
            channel_id = str(
                entry.get("channel_id")
                or entry.get("uploader_id")
                or entry.get("id")
                or ""
            ).strip()
            url = raw_url
            if not (isinstance(url, str) and url.startswith("http")):
                if raw_entry_url.startswith("@"):
                    url = f"https://www.youtube.com/{raw_entry_url}"
                elif channel_id.startswith("@"):
                    url = f"https://www.youtube.com/{channel_id}"
                elif channel_id.startswith("UC"):
                    url = f"https://www.youtube.com/channel/{channel_id}"
            if not (
                isinstance(url, str)
                and (
                    "/channel/" in url
                    or "/c/" in url
                    or "/user/" in url
                    or re.search(r"youtube\.com/@[^/?#]+/?", url)
                )
            ):
                continue
            item_id = channel_id or url
            key = item_id or url
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "title": title,
                    "channel": title,
                    "id": item_id,
                    "url": url.rstrip("/"),
                    "views": None,
                    "likes": None,
                    "kind": "channel",
                }
            )
            if len(results) >= limit:
                break
        return results

    def _extract_youtube_channel_videos(self, entries):
        results = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("_type") or entry.get("ie_key") or "").lower()
            if "channel" in kind:
                continue
            title = str(entry.get("title") or "").strip()
            if not title or title.lower() in {"deleted video", "private video"}:
                continue
            raw_url = self._youtube_abs_url(
                entry.get("webpage_url")
                or entry.get("url")
                or entry.get("original_url")
                or ""
            )
            raw_entry_url = str(entry.get("url") or "").strip()
            raw_entry_video_id = ""
            if (
                raw_entry_url
                and not raw_entry_url.startswith(("http://", "https://", "/", "@"))
                and "/" not in raw_entry_url
            ):
                raw_entry_video_id = raw_entry_url
            video_id = (
                entry.get("id")
                or entry.get("display_id")
                or self._youtube_video_id_from_url(raw_url)
                or raw_entry_video_id
            )
            video_id = str(video_id or "").strip()
            parsed_id = self._youtube_video_id_from_url(raw_url)
            if raw_url.startswith("http") and parsed_id:
                url = raw_url
                video_id = parsed_id
            elif video_id and not video_id.startswith(("UC", "@")):
                url = f"https://www.youtube.com/watch?v={video_id}"
            else:
                continue
            key = video_id or url
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "title": title,
                    "channel": str(
                        entry.get("channel")
                        or entry.get("uploader")
                        or entry.get("channel_title")
                        or self._youtube_channel_title
                        or ""
                    ).strip(),
                    "id": video_id or key,
                    "url": url,
                    "views": entry.get("view_count"),
                    "likes": entry.get("like_count"),
                    "kind": "video",
                }
            )
        return results

    def _youtube_stats_missing(self, results):
        return any(
            isinstance(item, dict)
            and not self._is_youtube_channel_result(item)
            and (item.get("views") is None or item.get("likes") is None)
            for item in results
        )

    def _youtube_result_header_title(self, width):
        if self._youtube_view == "channel" and self._youtube_channel_title:
            title = self._truncate(self._youtube_channel_title, max(1, width - 7))
            return f"{title} Videos"
        return "YouTube Result"

    def _start_youtube_stats_enrichment(self, query, results):
        items = [
            {
                "id": item.get("id"),
                "url": item.get("url"),
            }
            for item in results
            if (
                isinstance(item, dict)
                and not self._is_youtube_channel_result(item)
                and (item.get("id") or item.get("url"))
            )
        ]
        if not items or not self._yt_dlp_available():
            return False
        self._youtube_stats_request_id += 1
        request_id = self._youtube_stats_request_id
        self._youtube_stats_loading = True
        self._pending_youtube_stats_result = None
        self._pending_youtube_stats_error = None

        def worker():
            stats = {}
            def fetch_one(item):
                video_id = item.get("id")
                target = item.get("url") or (
                    f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
                )
                if not target:
                    return None, None
                cmd = self._yt_dlp_cmd(
                    "--skip-download",
                    "--no-warnings",
                    "--print",
                    "%(id)s\t%(view_count)s\t%(like_count)s",
                    "--no-playlist",
                    target,
                )
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    return None, None
                lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
                if not lines:
                    return None, None
                parts = lines[-1].split("\t")
                sid = parts[0] if parts else video_id
                if not sid:
                    return None, None
                return sid, {
                    "views": self._coerce_yt_int(parts[1] if len(parts) > 1 else None),
                    "likes": self._coerce_yt_int(parts[2] if len(parts) > 2 else None),
                }

            try:
                with ThreadPoolExecutor(max_workers=min(YT_STATS_WORKERS, len(items))) as pool:
                    futures = [pool.submit(fetch_one, item) for item in items]
                    for future in as_completed(futures):
                        sid, values = future.result()
                        if sid and values:
                            stats[sid] = values
                self._pending_youtube_stats_result = (request_id, query, stats)
            except Exception as exc:
                self._pending_youtube_stats_error = (request_id, query, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _yt_dlp_cmd(self, *args, use_cookies=False, use_js_runtime=True):
        cmd = [YT_DLP_BIN, "--ignore-config"]
        if YT_DLP_IMPERSONATE:
            cmd.extend(["--impersonate", YT_DLP_IMPERSONATE])
        if use_js_runtime and YT_DLP_JS_RUNTIME:
            cmd.extend(["--js-runtimes", YT_DLP_JS_RUNTIME])
        cmd.extend(
            [
                "--no-cache-dir",
                "--retries",
                YT_DLP_RETRIES,
                "--fragment-retries",
                YT_DLP_RETRIES,
                "--extractor-retries",
                YT_DLP_RETRIES,
                "--socket-timeout",
                "10",
            ]
        )
        if use_cookies and YT_DLP_COOKIES_FROM_BROWSER:
            cmd.extend(["--cookies-from-browser", YT_DLP_COOKIES_FROM_BROWSER])
        cmd.extend(args)
        return cmd

    @staticmethod
    def _terminate_proc(proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass

    def _run_ytdlp_capture(self, cmd, proc_attr=None):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc_attr:
            setattr(self, proc_attr, proc)
        stdout, stderr = proc.communicate()
        if proc_attr and getattr(self, proc_attr, None) is proc:
            setattr(self, proc_attr, None)
        proc.stdout = stdout
        proc.stderr = stderr
        return proc

    def _set_youtube_download_progress(
        self, request_id, phase, percent=None, speed="", eta="", title=""
    ):
        if request_id != self._youtube_download_request_id:
            return
        self.youtube_download_progress = {
            "phase": str(phase or "Downloading"),
            "percent": percent,
            "speed": str(speed or "").strip(),
            "eta": str(eta or "").strip(),
            "title": str(title or "").strip(),
        }
        if self.stdscr is not None:
            self.dirty = True
        wakeup = getattr(self, "_ui_wakeup", None)
        if callable(wakeup):
            try:
                wakeup()
            except Exception:
                pass

    @staticmethod
    def _parse_ytdlp_progress_line(line):
        marker = "__ANTARCTITEEX_PROGRESS__"
        if marker not in str(line):
            return None
        payload = str(line).split(marker, 1)[1].strip()
        parts = payload.split("\t")
        percent_text = re.sub(r"\x1b\[[0-9;]*m", "", parts[0]).strip()
        try:
            percent = float(percent_text.rstrip("%"))
        except (TypeError, ValueError):
            percent = None
        if percent is not None:
            percent = max(0.0, min(100.0, percent))
        clean = lambda value: "" if value.strip() in {"", "NA", "N/A"} else value.strip()
        return {
            "percent": percent,
            "speed": clean(parts[1]) if len(parts) > 1 else "",
            "eta": clean(parts[2]) if len(parts) > 2 else "",
        }

    def _run_ytdlp_with_progress(self, cmd, request_id, title=""):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            errors="replace",
        )
        self._youtube_download_proc = proc
        output = []
        try:
            for line in proc.stdout or ():
                stripped = line.rstrip("\r\n")
                output.append(stripped)
                progress = self._parse_ytdlp_progress_line(stripped)
                if progress is not None:
                    self._set_youtube_download_progress(
                        request_id,
                        "Downloading",
                        progress["percent"],
                        progress["speed"],
                        progress["eta"],
                        title,
                    )
            returncode = proc.wait()
        finally:
            if self._youtube_download_proc is proc:
                self._youtube_download_proc = None
        combined = "\n".join(output)
        return SimpleNamespace(
            returncode=returncode,
            stdout=combined,
            stderr=combined,
        )

    def _run_ytdlp_progress_with_format_fallback(self, cmd, request_id, title=""):
        proc = self._run_ytdlp_with_progress(cmd, request_id, title)
        if proc.returncode == 0 or not self._yt_dlp_requested_format_unavailable(proc):
            return proc
        return self._run_ytdlp_with_progress(
            self._without_ytdlp_format(cmd), request_id, title
        )

    def _run_ytdlp_progress_with_cookie_retry(
        self, make_cmd, request_id, title=""
    ):
        proc = self._run_ytdlp_progress_with_format_fallback(
            make_cmd(False), request_id, title
        )
        if proc.returncode == 0 or not self._yt_dlp_needs_cookie_retry(proc):
            return proc
        self._set_youtube_download_progress(
            request_id, "Retrying with browser cookies", 0.0, title=title
        )
        return self._run_ytdlp_progress_with_format_fallback(
            make_cmd(True), request_id, title
        )

    def _yt_dlp_available(self):
        if os.path.isabs(YT_DLP_BIN):
            return os.path.exists(YT_DLP_BIN) and os.access(YT_DLP_BIN, os.X_OK)
        return shutil.which(YT_DLP_BIN) is not None

    def _yt_dlp_error(self, proc):
        err = (proc.stderr or proc.stdout or "yt-dlp failed").strip()
        if "Sign in to confirm" in err:
            browser = YT_DLP_COOKIES_FROM_BROWSER.title()
            return f"YouTube still wants verification. Sign into YouTube in {browser}, then retry."
        if "HTTP Error 403" in err or "Forbidden" in err:
            return "YouTube blocked the media request"
        if "Signature solving failed" in err or "n challenge solving failed" in err:
            return "yt-dlp needs QuickJS/EJS to solve YouTube signatures"
        if "Only images are available" in err or "downloaded file is empty" in err:
            return "YouTube returned no playable audio/video formats"
        if "--cookies-from-browser" in err or "cookies" in err.lower():
            browser = YT_DLP_COOKIES_FROM_BROWSER.title()
            return f"Could not read {browser} cookies for yt-dlp"
        if "Requested format is not available" in err:
            return "YouTube did not offer the requested audio format"
        return err.splitlines()[-1][:120]

    @staticmethod
    def _yt_dlp_needs_cookie_retry(proc):
        err = (getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "").lower()
        return any(
            text in err
            for text in (
                "sign in to confirm",
                "confirm you're not a bot",
                "http error 403",
                "forbidden",
                "this video is unavailable",
                "caller does not have permission",
            )
        )

    def _yt_dlp_requested_format_unavailable(self, proc):
        err = (proc.stderr or proc.stdout or "").strip()
        return "Requested format is not available" in err

    def _without_ytdlp_format(self, cmd):
        fallback = []
        skip_next = False
        for arg in cmd:
            if skip_next:
                skip_next = False
                continue
            if arg in ("-f", "--format"):
                skip_next = True
                continue
            fallback.append(arg)
        return fallback

    def _run_ytdlp_with_format_fallback(self, cmd):
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0 or not self._yt_dlp_requested_format_unavailable(proc):
            return proc
        fallback_cmd = self._without_ytdlp_format(cmd)
        return subprocess.run(
            fallback_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def _run_ytdlp_capture_with_format_fallback(self, cmd, proc_attr=None):
        proc = self._run_ytdlp_capture(cmd, proc_attr)
        if proc.returncode == 0 or not self._yt_dlp_requested_format_unavailable(proc):
            return proc
        return self._run_ytdlp_capture(self._without_ytdlp_format(cmd), proc_attr)

    def _run_ytdlp_with_cookie_retry(self, make_cmd):
        proc = self._run_ytdlp_with_format_fallback(make_cmd(False))
        if proc.returncode == 0 or not self._yt_dlp_needs_cookie_retry(proc):
            return proc
        return self._run_ytdlp_with_format_fallback(make_cmd(True))

    def _run_ytdlp_capture_with_cookie_retry(self, make_cmd, proc_attr=None):
        proc = self._run_ytdlp_capture_with_format_fallback(make_cmd(False), proc_attr)
        if proc.returncode == 0 or not self._yt_dlp_needs_cookie_retry(proc):
            return proc
        return self._run_ytdlp_capture_with_format_fallback(make_cmd(True), proc_attr)

    def _find_downloaded_ext(self, folder, ext):
        suffix = "." + ext.lower().lstrip(".")
        for root, _, filenames in os.walk(folder):
            for filename in filenames:
                if filename.lower().endswith(suffix):
                    return os.path.join(root, filename)
        return ""

    def _find_downloaded_media(self, folder):
        preferred = (".m4a", ".mp3", ".ogg", ".opus", ".webm", ".wav")
        found = []
        for root, _, filenames in os.walk(folder):
            for filename in filenames:
                lower = filename.lower()
                if lower.endswith(".part") or lower.endswith(".ytdl"):
                    continue
                path = os.path.join(root, filename)
                if any(lower.endswith(ext) for ext in preferred):
                    found.append(path)
        if not found:
            return ""
        order = {ext: i for i, ext in enumerate(preferred)}
        found.sort(key=lambda p: order.get(os.path.splitext(p)[1].lower(), 99))
        return found[0]

    def _convert_preview_to_mp3(self, path):
        if not shutil.which("ffmpeg"):
            return ""
        root, ext = os.path.splitext(path)
        if ext.lower() == ".mp3":
            return path
        target = f"{root}.preview.mp3"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            path,
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            YT_PREVIEW_AUDIO_QUALITY,
            target,
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and os.path.exists(target):
            return target
        return ""

    def _preview_playback_path(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            return path
        converted = self._convert_preview_to_mp3(path)
        return converted or path

    def _audio_duration_ms(self, path):
        if shutil.which("ffprobe"):
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=4,
                    check=False,
                )
                if proc.returncode == 0:
                    seconds = float((proc.stdout or "").strip() or 0)
                    if seconds > 0:
                        return max(1, int(seconds * 1000))
            except Exception:
                pass
        try:
            snd = pygame.mixer.Sound(path)
            duration_ms = max(1, int(snd.get_length() * 1000))
            del snd
            return duration_ms
        except Exception:
            return 0

    def _run_youtube_search_json(self, request_id, *base_args):
        if request_id != self._youtube_search_request_id:
            return None
        proc = self._run_ytdlp_capture_with_cookie_retry(
            lambda use_cookies: self._yt_dlp_cmd(
                *base_args, use_cookies=use_cookies
            ),
            "_youtube_search_proc",
        )
        if proc.returncode != 0:
            raise RuntimeError(self._yt_dlp_error(proc))
        return json.loads(proc.stdout or "{}")

    def _merge_youtube_results(self, *groups):
        merged = []
        seen = set()
        for group in groups:
            for item in group or []:
                if not isinstance(item, dict):
                    continue
                key = (
                    self._youtube_result_kind(item),
                    str(item.get("id") or ""),
                    str(item.get("url") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged.append(dict(item))
        return merged

    def _start_youtube_result_search(self):
        query = self._youtube_query_from_search()
        self._youtube_search_debounce_at = 0.0
        self._youtube_search_pending_query = ""
        if not query:
            self._reset_youtube_channel_view()
            self.youtube_results = []
            self._youtube_results_query = ""
            self._youtube_results_loading = False
            return False
        if self._youtube_view != "search":
            self._reset_youtube_channel_view()
        cache_key = self._youtube_cache_key(query=query)
        limit = self._youtube_result_limit()
        if cache_key == self._youtube_results_query and (
            self._youtube_results_loading or len(self.youtube_results) >= limit
        ):
            return False
        cached = self._youtube_search_cache.get(cache_key)
        if cached and len(cached) >= limit:
            self._youtube_results_query = cache_key
            self._youtube_results_loading = False
            self.youtube_results = [dict(r) for r in cached]
            self._sort_youtube_results()
            if self._youtube_stats_missing(self.youtube_results):
                self._start_youtube_stats_enrichment(cache_key, self.youtube_results)
            self.status_msg = f"{len(self.youtube_results)} YouTube result"
            if len(self.youtube_results) != 1:
                self.status_msg += "s"
            self._needs_redraw_hdr = True
            self._needs_redraw_lst = True
            self.dirty = True
            return True

        self._youtube_search_request_id += 1
        self._youtube_stats_request_id += 1
        self._terminate_proc(self._youtube_search_proc)
        request_id = self._youtube_search_request_id
        self._youtube_results_query = cache_key
        self._youtube_results_loading = True
        self._youtube_stats_loading = False
        self.youtube_results = []
        self.status_msg = f"Searching YouTube: {query}"
        self._pending_youtube_search_result = None
        self._pending_youtube_search_error = None
        self._needs_redraw_hdr = True
        self._needs_redraw_lst = True
        self.dirty = True

        def worker():
            try:
                video_limit = max(1, limit)
                video_args = (
                    "--flat-playlist",
                    "--dump-single-json",
                    "--playlist-end",
                    str(video_limit),
                    f"ytsearch{video_limit}:{query}",
                )
                data = self._run_youtube_search_json(request_id, *video_args)
                if data is None:
                    return
                entries = data.get("entries", []) if isinstance(data, dict) else []
                video_results = [
                    item
                    for item in self._extract_youtube_search_results(entries, limit)
                    if not self._is_youtube_channel_result(item)
                ]
                self._pending_youtube_search_result = (
                    request_id,
                    cache_key,
                    video_results[:limit],
                    False,
                )

                channel_limit = min(YT_SEARCH_CHANNEL_LIMIT, max(2, limit // 8))
                channel_results = self._extract_youtube_channels_from_video_entries(
                    entries, channel_limit
                )
                channel_args = (
                    "--flat-playlist",
                    "--dump-single-json",
                    "--playlist-end",
                    str(channel_limit),
                    self._youtube_search_url(query, "EgIQAg=="),
                )
                try:
                    channel_data = self._run_youtube_search_json(
                        request_id, *channel_args
                    )
                    if channel_data is None:
                        return
                    channel_entries = (
                        channel_data.get("entries", [])
                        if isinstance(channel_data, dict)
                        else []
                    )
                    direct_channel_results = self._extract_youtube_channel_search_results(
                        channel_entries, channel_limit
                    )
                    channel_results = self._merge_youtube_results(
                        channel_results, direct_channel_results
                    )
                except Exception:
                    pass

                results = self._merge_youtube_results(video_results, channel_results)
                self._pending_youtube_search_result = (
                    request_id,
                    cache_key,
                    results,
                    True,
                )
            except Exception as exc:
                self._pending_youtube_search_error = (request_id, cache_key, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _open_youtube_channel(self, result):
        target = self._youtube_channel_videos_target(result)
        title = result.get("title", "Channel") if isinstance(result, dict) else "Channel"
        if not target:
            self.status_msg = "Could not open YouTube channel"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False
        if not self._yt_dlp_available():
            self.status_msg = "yt-dlp is required for YouTube channel videos"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False
        cache_key = self._youtube_cache_key(channel_url=target)
        cached = self._youtube_search_cache.get(cache_key)
        self._youtube_view = "channel"
        self._youtube_channel_title = title
        self._youtube_channel_url = target
        self.scroll = 0
        if cached:
            self.youtube_results = [dict(r) for r in cached]
            self._youtube_results_query = cache_key
            self._youtube_results_loading = False
            self._sort_youtube_results()
            visible_results = self.youtube_results[: self._youtube_result_limit()]
            if self._youtube_stats_missing(visible_results):
                self._start_youtube_stats_enrichment(cache_key, visible_results)
            self.status_msg = f"{len(self.youtube_results)} videos from {title}"
            self._needs_redraw_hdr = True
            self._needs_redraw_tabs = True
            self._needs_redraw_lst = True
            self.dirty = True
            return True

        self._youtube_search_request_id += 1
        self._youtube_stats_request_id += 1
        self._terminate_proc(self._youtube_search_proc)
        request_id = self._youtube_search_request_id
        self._youtube_results_query = cache_key
        self._youtube_results_loading = True
        self._youtube_stats_loading = False
        self.youtube_results = []
        self.status_msg = f"Loading channel videos: {title}"
        self._pending_youtube_search_result = None
        self._pending_youtube_search_error = None
        self._needs_redraw_hdr = True
        self._needs_redraw_tabs = True
        self._needs_redraw_lst = True
        self.dirty = True

        def worker():
            try:
                base_args = (
                    "--flat-playlist",
                    "--dump-single-json",
                    target,
                )
                if request_id != self._youtube_search_request_id:
                    return
                proc = self._run_ytdlp_capture_with_cookie_retry(
                    lambda use_cookies: self._yt_dlp_cmd(
                        *base_args, use_cookies=use_cookies
                    ),
                    "_youtube_search_proc",
                )
                if proc.returncode != 0:
                    raise RuntimeError(self._yt_dlp_error(proc))
                data = json.loads(proc.stdout)
                entries = data.get("entries", []) if isinstance(data, dict) else []
                results = self._extract_youtube_channel_videos(entries)
                self._pending_youtube_search_result = (
                    request_id,
                    cache_key,
                    results,
                )
            except Exception as exc:
                self._pending_youtube_search_error = (request_id, cache_key, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _youtube_back_to_search(self):
        if self._youtube_view != "channel":
            return False
        query = self._youtube_query_from_search()
        self._youtube_view = "search"
        self._youtube_channel_title = ""
        self._youtube_channel_url = ""
        self.scroll = 0
        cache_key = self._youtube_cache_key(query=query)
        cached = self._youtube_search_cache.get(cache_key)
        if cached:
            self.youtube_results = [dict(r) for r in cached]
            self._youtube_results_query = cache_key
            self._youtube_results_loading = False
            self._sort_youtube_results()
            self.status_msg = f"{len(self.youtube_results)} YouTube results"
        else:
            self.youtube_results = []
            self._youtube_results_query = ""
            self._start_youtube_result_search()
        self._needs_redraw_hdr = True
        self._needs_redraw_tabs = True
        self._needs_redraw_lst = True
        self.dirty = True
        return True

    def _start_youtube_preview(self, result=None):
        if self._shutting_down:
            return False
        query = self._youtube_query_from_search()
        target = self._youtube_video_target(result) if result is not None else query
        title_hint = result.get("title", "") if isinstance(result, dict) else ""
        if not target:
            return False
        if self._youtube_loading:
            self._youtube_request_id += 1
            self._youtube_loading = False
            self._terminate_proc(self._youtube_preview_proc)
        if not self._yt_dlp_available():
            self.status_msg = "yt-dlp is required for YouTube preview"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False
        if isinstance(result, dict):
            self._youtube_active_result = dict(result)
        self._youtube_request_id += 1
        request_id = self._youtube_request_id
        self._youtube_loading = True
        self.status_msg = f"Loading YouTube preview: {title_hint or target}"
        self._pending_youtube_result = None
        self._pending_youtube_error = None
        self._needs_redraw_hdr = True
        self.dirty = True

        def worker():
            temp_dir = tempfile.mkdtemp(prefix="play-youtube-")
            outtmpl = os.path.join(temp_dir, "preview.%(ext)s")
            try:
                base_args = (
                    "--no-playlist",
                    "--default-search",
                    "ytsearch1",
                    "--no-mtime",
                    "-N",
                    YT_DLP_CONCURRENT_FRAGMENTS,
                    "--print",
                    "after_move:filepath",
                    "-o",
                    outtmpl,
                    target,
                )
                if request_id != self._youtube_request_id:
                    return
                proc = self._run_ytdlp_capture_with_cookie_retry(
                    lambda use_cookies: self._yt_dlp_cmd(
                        "-f",
                        YT_PREVIEW_FORMAT,
                        *base_args,
                        use_cookies=use_cookies,
                    ),
                    "_youtube_preview_proc",
                )
                if proc.returncode != 0:
                    raise RuntimeError(self._yt_dlp_error(proc))
                paths = [
                    line.strip() for line in proc.stdout.splitlines() if line.strip()
                ]
                path = next(
                    (
                        p
                        for p in reversed(paths)
                        if os.path.exists(p) and not p.lower().endswith(".part")
                    ),
                    "",
                )
                if not path:
                    path = self._find_downloaded_media(temp_dir)
                if not path or not os.path.exists(path):
                    raise RuntimeError("yt-dlp did not produce playable audio")
                path = self._preview_playback_path(path)
                if not path or not os.path.exists(path):
                    raise RuntimeError("Could not prepare preview audio")
                title = title_hint or os.path.splitext(os.path.basename(path))[0]
                self._pending_youtube_result = (request_id, temp_dir, path, title)
            except Exception as exc:
                shutil.rmtree(temp_dir, ignore_errors=True)
                self._pending_youtube_error = (request_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _start_youtube_download(self, result=None, save_name=None):
        if result is None:
            result = self._youtube_download_result()
        target = self._youtube_video_target(result)
        if not target:
            self.status_msg = "No YouTube result to download"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False
        if self._youtube_download_loading:
            return False
        if not self._yt_dlp_available():
            self.status_msg = "yt-dlp is required for download"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False
        if not shutil.which("ffmpeg"):
            self.status_msg = "ffmpeg is required for WAV to MP3"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False
        if self._youtube_loading:
            self._youtube_request_id += 1
            self._youtube_loading = False

        self._youtube_download_request_id += 1
        request_id = self._youtube_download_request_id
        self._youtube_download_loading = True
        self._youtube_active_result = dict(result) if isinstance(result, dict) else None
        title_hint = result.get("title", target) if isinstance(result, dict) else target
        requested_name = (
            self._clean_mp3_basename(save_name) if save_name is not None else ""
        )
        self.status_msg = f"Downloading: {title_hint}"
        self._set_youtube_download_progress(
            request_id, "Preparing download", 0.0, title=title_hint
        )
        self._pending_youtube_download_result = None
        self._pending_youtube_download_error = None
        self._needs_redraw_hdr = True
        self.dirty = True

        def worker():
            temp_dir = tempfile.mkdtemp(prefix="play-youtube-download-")
            outtmpl = os.path.join(temp_dir, "%(title).160s [%(id)s].%(ext)s")
            try:
                def make_cmd(use_cookies):
                    return self._yt_dlp_cmd(
                        "--no-playlist",
                        "-N",
                        YT_DLP_CONCURRENT_FRAGMENTS,
                        "-f",
                        YT_DOWNLOAD_FORMAT,
                        "-x",
                        "--audio-format",
                        "wav",
                        "--audio-quality",
                        "0",
                        "--progress",
                        "--newline",
                        "--no-color",
                        "--progress-template",
                        "download:__ANTARCTITEEX_PROGRESS__%(progress._percent_str)s\t%(progress._speed_str)s\t%(progress._eta_str)s",
                        "--print",
                        "after_move:__ANTARCTITEEX_FILE__%(filepath)s",
                        "-o",
                        outtmpl,
                        target,
                        use_cookies=use_cookies,
                    )

                proc = self._run_ytdlp_progress_with_cookie_retry(
                    make_cmd, request_id, title_hint
                )
                if proc.returncode != 0:
                    raise RuntimeError(self._yt_dlp_error(proc))
                paths = [
                    line.split("__ANTARCTITEEX_FILE__", 1)[1].strip()
                    for line in proc.stdout.splitlines()
                    if "__ANTARCTITEEX_FILE__" in line
                ]
                wav_path = next(
                    (p for p in reversed(paths) if p.lower().endswith(".wav")),
                    "",
                )
                if not wav_path:
                    for filename in os.listdir(temp_dir):
                        if filename.lower().endswith(".wav"):
                            wav_path = os.path.join(temp_dir, filename)
                            break
                if not wav_path or not os.path.exists(wav_path):
                    raise RuntimeError("yt-dlp did not produce a WAV")

                base = requested_name or os.path.splitext(os.path.basename(wav_path))[0]
                final_path = self._unique_path(os.path.join(self.folder, f"{base}.mp3"))
                self._set_youtube_download_progress(
                    request_id, "Converting to MP3", 100.0, title=title_hint
                )
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    wav_path,
                    "-codec:a",
                    "libmp3lame",
                    "-q:a",
                    "0",
                    final_path,
                ]
                proc = subprocess.run(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    err = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
                    raise RuntimeError(err.splitlines()[-1][:120])
                if not os.path.exists(final_path):
                    raise RuntimeError("ffmpeg did not produce an MP3")
                self._set_youtube_download_progress(
                    request_id, "Finishing", 100.0, title=title_hint
                )
                title = requested_name or title_hint or os.path.splitext(
                    os.path.basename(final_path)
                )[0]
                self._pending_youtube_download_result = (
                    request_id,
                    temp_dir,
                    final_path,
                    title,
                    not bool(requested_name),
                )
            except Exception as exc:
                shutil.rmtree(temp_dir, ignore_errors=True)
                self._pending_youtube_download_error = (request_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _play_youtube_preview(self, temp_dir, path, title):
        if self._shutting_down:
            shutil.rmtree(temp_dir, ignore_errors=True)
            return
        if not self._ensure_mixer_ready():
            shutil.rmtree(temp_dir, ignore_errors=True)
            self.status_msg = "Audio mixer failed to initialize"
            self._needs_redraw_hdr = True
            self.dirty = True
            return
        self._suppress_completion_events()
        self._new_playback_session()
        self._cleanup_youtube_preview(stop_audio=True)
        self.play_tab = "YouTube"
        self._clear_pending_play_incs()
        self._pending_shuffle_next = None
        self._last_loop_idx = 0
        self._natural_loop_restart = False
        self._play_start = 0.0
        self._pause_offset = 0.0
        self._pause_start = 0.0
        self._song_len_ms = 0
        self._current_playback_path = ""
        self._current_volume_multiplier = 1.0
        self.paused = False
        self._paused_by_lock = False
        self._paused_by_speaker_safety = False
        self._youtube_temp_dir = temp_dir
        self._youtube_temp_path = path
        self.current = "Loading..."
        self.status_msg = "Loading YouTube preview"
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._needs_redraw_lst = True
        self.dirty = True
        try:
            try:
                pygame.mixer.music.load(path)
            except Exception:
                converted = self._convert_preview_to_mp3(path)
                if not converted:
                    raise
                path = converted
                self._youtube_temp_path = path
                pygame.mixer.music.load(path)
            self._current_playback_path = path
            self._song_len_ms = self._audio_duration_ms(path)
            self._apply_volume(force=True)
            pygame.mixer.music.play(loops=0)
            self._apply_volume(force=True)
            time.sleep(0.03)
            if not pygame.mixer.music.get_busy():
                raise RuntimeError("preview audio did not start")
            self.current = f"YouTube: {title}"
            self.status_msg = (
                "YouTube preview will be deleted when it ends "
                "or you play another track"
            )
            self._play_start = time.monotonic()
        except Exception:
            self.status_msg = "Could not play YouTube preview"
            self._cleanup_youtube_preview(stop_audio=True)
        self._update_timeout()

    def play(self, name):
        if self._shutting_down:
            return
        if not self._ensure_mixer_ready():
            self.status_msg = "Audio mixer failed to initialize"
            self._needs_redraw_hdr = True
            self.dirty = True
            return
        if (
            self.song_switch_locked
            and self.current in self._all_songs_set
        ):
            self.play_mode = "loop"
            self._pending_shuffle_next = None
            self.status_msg = "MP3 switch lock is on"
            self._needs_redraw_hdr = True
            self.dirty = True
            return
        self._suppress_completion_events()
        self._new_playback_session()
        if self.apple_radio_active:
            self._stop_apple_radio_audio()
        self._clear_radio_resume_state()
        self.apple_radio_enabled = False
        self.apple_radio_active = ""
        self.apple_radio_now = ""
        self.apple_radio_music_now = ""
        self._youtube_request_id += 1
        self._youtube_loading = False
        self._terminate_proc(self._youtube_preview_proc)
        self.status_msg = ""
        self._cleanup_youtube_preview(stop_audio=True)
        path = os.path.join(self.folder, name + ".mp3")
        if not os.path.exists(path):
            self._set_playback_error(name, "file not found")
            return
        self._use_viewed_playlist_for_playback()
        self._clear_pending_play_incs()
        self._pending_shuffle_next = None
        self._last_loop_idx = 0
        self._natural_loop_restart = False
        self._play_start = 0.0
        self._pause_offset = 0.0
        self._pause_start = 0.0
        self._song_len_ms = 0
        self._current_playback_path = ""
        self._current_volume_multiplier = 1.0
        self.paused = False
        self._paused_by_lock = False
        self._paused_by_speaker_safety = False
        self.current = "Loading..."
        self.dirty = True
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._needs_redraw_lst = True
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
        try:
            self._current_volume_multiplier = self._cached_volume_multiplier(name)
            self._current_playback_path = path
            self._song_len_ms = self._cached_duration_ms(name)
            pygame.mixer.music.load(path)
            self._apply_volume(force=True)
            pygame.mixer.music.play(loops=0)
            self._apply_volume(force=True)
            self.current = name
            self._play_start = time.monotonic()
            self._queue_volume_multiplier_compute(name)
        except Exception as exc:
            self.current = "None"
            self._song_len_ms = 0
            self._current_playback_path = ""
            self._set_playback_error(name, exc)
        self._update_timeout()

    def _play_next_shuffle(self, name):
        if self._shutting_down:
            return
        if not self._ensure_mixer_ready():
            self.status_msg = "Audio mixer failed to initialize"
            self._needs_redraw_hdr = True
            self.dirty = True
            return
        if self.song_switch_locked:
            self.play_mode = "loop"
            self._pending_shuffle_next = None
            self._restart_current_audio()
            return
        self._suppress_completion_events()
        self._new_playback_session()
        self._cleanup_youtube_preview(stop_audio=True)
        path = os.path.join(self.folder, name + ".mp3")
        if not os.path.exists(path):
            self._set_playback_error(name, "file not found")
            return
        self._clear_pending_play_incs()
        self._pending_shuffle_next = None
        self._last_loop_idx = 0
        self._natural_loop_restart = False
        self._play_start = 0.0
        self._pause_offset = 0.0
        self._pause_start = 0.0
        self._song_len_ms = 0
        self._current_playback_path = ""
        self.paused = False
        self._paused_by_lock = False
        self.current = "Loading..."
        self.dirty = True
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._needs_redraw_lst = True
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
        try:
            self._current_volume_multiplier = self._cached_volume_multiplier(name)
            self._current_playback_path = path
            self._song_len_ms = self._cached_duration_ms(name)
            pygame.mixer.music.load(path)
            self._apply_volume(force=True)
            pygame.mixer.music.play(loops=0)
            self._apply_volume(force=True)
            self.current = name
            self._play_start = time.monotonic()
            self._queue_volume_multiplier_compute(name)
        except Exception as exc:
            self.current = "None"
            self._song_len_ms = 0
            self._current_playback_path = ""
            self._set_playback_error(name, exc)
        self._update_timeout()

    def _restart_current_audio(self, rewind_ms=0, target_ms=None, suppress_completion=True):
        if self._shutting_down:
            return
        if not self._ensure_mixer_ready():
            self.status_msg = "Audio mixer failed to initialize"
            self._needs_redraw_hdr = True
            self.dirty = True
            return
        if self.current in ("None", "Loading..."):
            return
        if suppress_completion:
            self._suppress_completion_events()
        self._new_playback_session()
        self._natural_loop_restart = False
        path = os.path.join(self.folder, self.current + ".mp3")
        if not os.path.exists(path):
            self._set_playback_error(self.current, "file not found")
            return
        was_paused = self.paused
        if target_ms is None:
            pos_ms = max(0, self._current_pos_in_song()[0] - max(0, int(rewind_ms)))
        else:
            pos_ms = max(0, min(int(target_ms), max(0, self._song_len_ms - 1)))
        loops = 0
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
        except Exception:
            pass
        try:
            self._current_volume_multiplier = self._cached_volume_multiplier(self.current)
            self._queue_volume_multiplier_compute(self.current)
            self._current_playback_path = path
            duration_ms = self._cached_duration_ms(self.current)
            if duration_ms > 0:
                self._song_len_ms = duration_ms
                pos_ms = min(pos_ms, max(0, self._song_len_ms - 1))
            pygame.mixer.music.load(path)
            self._apply_volume(force=True)
            start_s = max(0.0, pos_ms / 1000)
            try:
                pygame.mixer.music.play(loops=loops, start=start_s)
            except Exception:
                pygame.mixer.music.play(loops=loops)
                if start_s > 0:
                    try:
                        pygame.mixer.music.set_pos(start_s)
                    except Exception:
                        pass
            self._apply_volume(force=True)
            now = time.monotonic()
            self._play_start = now - (pos_ms / 1000)
            self._pause_offset = 0.0
            self._pause_start = now if was_paused else 0.0
            self._last_loop_idx = 0
            if was_paused:
                pygame.mixer.music.pause()
            self.paused = was_paused
        except Exception as exc:
            failed_name = self.current
            self.current = "None"
            self._song_len_ms = 0
            self._current_playback_path = ""
            self.paused = False
            self._set_playback_error(failed_name, exc)
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self.dirty = True
        self._update_timeout()

    def _ensure_mixer_ready(self):
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(44100, -16, 2, AUDIO_BUFFER_SIZE)
                self._last_volume_apply_at = 0.0
            return True
        except Exception:
            return False

    def _switch_play_mode(self, rewind_ms=0, target_ms=None):
        self._use_viewed_playlist_for_playback()
        if self.song_switch_locked:
            self.play_mode = "loop"
            self._pending_shuffle_next = None
            self.status_msg = "MP3 switch lock is on"
            self._needs_redraw_hdr = True
            self.dirty = True
            return
        self._suppress_completion_events()
        self.play_mode = "shuffle" if self.play_mode == "loop" else "loop"
        self._pending_shuffle_next = None
        self._rebuild_play_pool()
        self._restart_current_audio(rewind_ms=rewind_ms, target_ms=target_ms)
        self._needs_redraw_hdr = True
        self.dirty = True

    def toggle_pause(self):
        if self.current in ("None", "Loading..."):
            return
        if self.paused:
            if (
                self._paused_by_speaker_safety
                and self.non_speaker_mode
                and self._is_builtin_speaker_name(self._current_output_device)
            ):
                self.status_msg = "Still paused: built-in speakers active"
                self.dirty = True
                self._needs_redraw_hdr = True
                return
            self._paused_by_speaker_safety = False
            self._pause_offset += time.monotonic() - self._pause_start
            try:
                pygame.mixer.music.unpause()
                self._apply_volume(force=True)
            except Exception:
                pass
            self.paused = False
        else:
            self._paused_by_speaker_safety = False
            self._pause_start = time.monotonic()
            try:
                pygame.mixer.music.pause()
            except Exception:
                pass
            self.paused = True
        self.dirty = True
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self._update_timeout()

    def _apply_volume(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_volume_apply_at < VOLUME_ENFORCE_S:
            return
        try:
            if not self._ensure_mixer_ready():
                return
            multiplier = max(0.0, min(1.0, float(self._current_volume_multiplier)))
            pygame.mixer.music.set_volume(max(0.0, min(1.0, self.vol * multiplier)))
            self._last_volume_apply_at = now
        except Exception:
            pass

    def _set_playback_error(self, name, error, notify=True):
        detail = " ".join(str(error or "unknown audio error").split())
        if len(detail) > 120:
            detail = detail[:117] + "..."
        self.status_msg = f"Could not play '{name}.mp3': {detail}"
        self._needs_redraw_hdr = True
        self._needs_redraw_bar = True
        self.dirty = True
        if notify:
            self._emit_notification(self.status_msg, severity="error", timeout=7)

    def _enforce_playing_volume(self):
        if self.current in ("None", "Loading...") or self.paused:
            return
        self._apply_volume(force=False)

    def _adjust_volume(self, steps):
        if not steps:
            return
        new_vol = round(min(1.0, max(0.0, self.vol + (0.1 * steps))), 1)
        if new_vol == self.vol:
            return
        self.vol = new_vol
        self._apply_volume(force=True)
        self._needs_redraw_hdr = True
        self.dirty = True

    def _process_pending(self):
        if self._shutting_down:
            return
        with self._play_inc_lock:
            has_play_incs = bool(self._pending_play_incs)
        if not (
            self._pending_media_toggle
            or has_play_incs
            or self._pending_shuffle_next is not None
            or self._natural_loop_restart
            or self._meta_dirty
            or self._pending_youtube_result is not None
            or self._pending_youtube_error is not None
            or self._pending_youtube_search_result is not None
            or self._pending_youtube_search_error is not None
            or self._pending_youtube_stats_result is not None
            or self._pending_youtube_stats_error is not None
            or self._pending_youtube_download_result is not None
            or self._pending_youtube_download_error is not None
            or self._pending_preview_cleanup
            or self._youtube_search_debounce_at
        ):
            return
        changed = False
        now = time.monotonic()
        if self._youtube_search_debounce_at:
            if now >= self._youtube_search_debounce_at:
                pending_query = self._youtube_search_pending_query
                current_query = self._youtube_query_from_search()
                self._youtube_search_debounce_at = 0.0
                self._youtube_search_pending_query = ""
                if pending_query and pending_query == current_query:
                    self._start_youtube_result_search()
            else:
                self.dirty = True
        if self._pending_preview_cleanup:
            self._cleanup_youtube_preview(stop_audio=True)
            self.status_msg = "Deleted YouTube preview"
            self._needs_redraw_hdr = True
            self._needs_redraw_bar = True
            self._needs_redraw_lst = True
            self.dirty = True
        if self._pending_youtube_error is not None:
            request_id, error = self._pending_youtube_error
            self._pending_youtube_error = None
            if request_id == self._youtube_request_id:
                self._youtube_loading = False
                self.status_msg = f"YouTube preview failed: {error}"
                self._emit_notification(
                    self.status_msg, severity="error", timeout=7
                )
                self._needs_redraw_hdr = True
                self.dirty = True
        if self._pending_youtube_search_error is not None:
            request_id, cache_key, error = self._pending_youtube_search_error
            self._pending_youtube_search_error = None
            if request_id == self._youtube_search_request_id:
                self._youtube_results_loading = False
                self.youtube_results = []
                self.status_msg = f"YouTube search failed: {error}"
                self._emit_notification(
                    self.status_msg, severity="error", timeout=7
                )
                self._needs_redraw_hdr = True
                self._needs_redraw_lst = True
                self.dirty = True
        if self._pending_youtube_search_result is not None:
            pending = self._pending_youtube_search_result
            if len(pending) == 4:
                request_id, cache_key, results, search_done = pending
            else:
                request_id, cache_key, results = pending
                search_done = True
            self._pending_youtube_search_result = None
            if request_id == self._youtube_search_request_id:
                self._youtube_results_loading = not search_done
                self._youtube_results_query = cache_key
                self.youtube_results = results
                self._sort_youtube_results()
                if results and search_done:
                    self._youtube_search_cache[cache_key] = [dict(r) for r in results]
                    if len(self._youtube_search_cache) > 24:
                        oldest = next(iter(self._youtube_search_cache))
                        self._youtube_search_cache.pop(oldest, None)
                    self._start_youtube_stats_enrichment(
                        cache_key, results[: self._youtube_result_limit()]
                    )
                self.status_msg = (
                    f"{len(results)} YouTube result"
                    f"{'' if len(results) == 1 else 's'}"
                )
                if not search_done:
                    self.status_msg += "..."
                self._needs_redraw_hdr = True
                self._needs_redraw_lst = True
                self.dirty = True
        if self._pending_youtube_stats_error is not None:
            request_id, query, _error = self._pending_youtube_stats_error
            self._pending_youtube_stats_error = None
            if request_id == self._youtube_stats_request_id:
                self._youtube_stats_loading = False
                self._needs_redraw_hdr = True
                self.dirty = True
        if self._pending_youtube_stats_result is not None:
            request_id, cache_key, stats = self._pending_youtube_stats_result
            self._pending_youtube_stats_result = None
            if request_id == self._youtube_stats_request_id:
                self._youtube_stats_loading = False
                by_id = {
                    item.get("id"): item
                    for item in self.youtube_results
                    if isinstance(item, dict) and item.get("id")
                }
                changed_stats = False
                for video_id, values in stats.items():
                    item = by_id.get(video_id)
                    if not item:
                        continue
                    if values.get("views") is not None:
                        item["views"] = values.get("views")
                        changed_stats = True
                    if values.get("likes") is not None:
                        item["likes"] = values.get("likes")
                        changed_stats = True
                if changed_stats:
                    self._sort_youtube_results()
                    self._youtube_search_cache[cache_key] = [
                        dict(r) for r in self.youtube_results
                    ]
                    self.status_msg = "YouTube stats loaded"
                self._needs_redraw_hdr = True
                self._needs_redraw_lst = True
                self.dirty = True
        if self._pending_youtube_download_error is not None:
            request_id, error = self._pending_youtube_download_error
            self._pending_youtube_download_error = None
            if request_id == self._youtube_download_request_id:
                self._youtube_download_loading = False
                self.youtube_download_progress = None
                self.status_msg = f"YouTube download failed: {error}"
                self._emit_notification(
                    self.status_msg, severity="error", timeout=7
                )
                self._needs_redraw_hdr = True
                self.dirty = True
        if self._pending_youtube_download_result is not None:
            pending_download = self._pending_youtube_download_result
            if len(pending_download) >= 5:
                request_id, temp_dir, final_path, title, prompt_for_name = (
                    pending_download
                )
            else:
                request_id, temp_dir, final_path, title = pending_download
                prompt_for_name = True
            self._pending_youtube_download_result = None
            if request_id == self._youtube_download_request_id:
                self._youtube_download_loading = False
                self.youtube_download_progress = None
                self._youtube_search_request_id += 1
                self._youtube_results_loading = False
                self.youtube_results = []
                self._youtube_active_result = None
                self._youtube_results_query = ""
                self.youtube_preview_enabled = False
                self._cleanup_youtube_preview(stop_audio=True)
                shutil.rmtree(temp_dir, ignore_errors=True)
                if prompt_for_name:
                    self._emit_notification(f"Downloaded '{title}'")
                    self._start_download_rename(final_path, title)
                else:
                    saved_name = os.path.splitext(os.path.basename(final_path))[0]
                    self.status_msg = f"Saved: {saved_name}"
                    self._emit_notification(self.status_msg)
                self._rebuild()
                self._layout()
                self._needs_redraw_hdr = True
                self._needs_redraw_lst = True
                self._needs_redraw_inp = True
                self.dirty = True
                changed = True
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)
        if self._pending_youtube_result is not None:
            request_id, temp_dir, path, title = self._pending_youtube_result
            self._pending_youtube_result = None
            if request_id == self._youtube_request_id:
                self._youtube_loading = False
                self._play_youtube_preview(temp_dir, path, title)
                changed = True
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)
        if self._pending_media_toggle:
            self._pending_media_toggle = False
            if self._paused_by_lock and not self._screen_locked:
                self._paused_by_lock = False
            self.toggle_pause()
        with self._play_inc_lock:
            pending_incs = self._pending_play_incs
            self._pending_play_incs = {}
        for name, n in pending_incs.items():
            if name in self._all_songs_set and n > 0:
                self._inc_plays(name, n)
                changed = True
        if self._pending_shuffle_next is not None:
            next_name = self._pending_shuffle_next
            self._pending_shuffle_next = None
            self._play_next_shuffle(next_name)
            changed = True
        if self._natural_loop_restart:
            self._natural_loop_restart = False
            if (
                self.play_mode == "loop"
                and not self.paused
                and self.current in self._all_songs_set
                and not pygame.mixer.music.get_busy()
            ):
                self._restart_current_audio(target_ms=0, suppress_completion=False)
                changed = True
        if self._meta_dirty:
            self._meta_dirty = False
            self._save_meta()
        if changed:
            self._rebuild()

    def _vis(self):
        try:
            return max(0, self.win_lst.getmaxyx()[0] - 3)
        except Exception:
            return 0

    def _max_scroll(self):
        if self.apple_radio_enabled:
            total = len(APPLE_RADIO_STATIONS)
        elif self.youtube_preview_enabled:
            total = len(self.youtube_results)
        else:
            total = len(self.songs)
        return max(0, total - self._vis())

    def _clamp_scroll(self):
        self.scroll = min(max(0, self.scroll), self._max_scroll())

    def _handle_list_scroll_key(self, key):
        page = max(1, self._vis() - 1)
        if key == curses.KEY_UP:
            self._scroll_up()
        elif key == curses.KEY_DOWN:
            self._scroll_down()
        elif key == curses.KEY_PPAGE:
            self._scroll_up(page)
        elif key == curses.KEY_NPAGE:
            self._scroll_down(page)
        elif key == curses.KEY_HOME:
            self.scroll = 0
        elif key == curses.KEY_END:
            self.scroll = self._max_scroll()
        else:
            return False
        self.dirty = True
        self._needs_redraw_lst = True
        return True

    def _scroll_up(self, n=1):
        if n < 0:
            return
        new_scroll = max(0, self.scroll - n)
        if new_scroll != self.scroll:
            self.scroll = new_scroll
            self.dirty = True
            self._needs_redraw_lst = True

    def _scroll_down(self, n=1):
        if n < 0:
            return
        new_scroll = min(self._max_scroll(), self.scroll + n)
        if new_scroll != self.scroll:
            self.scroll = new_scroll
            self.dirty = True
            self._needs_redraw_lst = True

    def _scrollbar_geometry(self):
        """Return global scrollbar geometry, or None when it is hidden."""
        try:
            ly, lx = self.win_lst.getbegyx()
            lh, lw = self.win_lst.getmaxyx()
            vis = max(0, lh - 3)
            maximum = self._max_scroll()
            if vis <= 0 or maximum <= 0:
                return None
            total = maximum + vis
            thumb_size = min(vis, max(1, int(vis * vis / total)))
            thumb_pos = int((vis - thumb_size) * self.scroll / maximum)
            return lx + lw - 3, ly + 2, vis, thumb_pos, thumb_size, maximum
        except Exception:
            return None

    def _in_scrollbar_gutter(self, mouse_y, mouse_x):
        """Keep the list's rightmost three columns exclusive to scrolling."""
        try:
            ly, lx = self.win_lst.getbegyx()
            lh, lw = self.win_lst.getmaxyx()
            gutter_left = lx + max(1, lw - 4)
            return (
                ly < mouse_y < ly + lh - 1
                and gutter_left <= mouse_x < lx + lw - 1
            )
        except Exception:
            return False

    def _drag_scrollbar_to(self, mouse_y):
        geometry = self._scrollbar_geometry()
        if geometry is None:
            self._scroll_dragging = False
            return False
        _, track_y, vis, _, thumb_size, maximum = geometry
        travel = vis - thumb_size
        if travel <= 0:
            new_scroll = 0
        else:
            thumb_pos = max(
                0, min(travel, mouse_y - track_y - self._scroll_drag_offset)
            )
            new_scroll = int(round(maximum * thumb_pos / travel))
        if new_scroll != self.scroll:
            self.scroll = new_scroll
            self.dirty = True
            self._needs_redraw_lst = True
        return True

    def _start_scrollbar_drag(self, mouse_y, mouse_x):
        geometry = self._scrollbar_geometry()
        if geometry is None:
            return False
        _, track_y, vis, thumb_pos, thumb_size, _ = geometry
        if not self._in_scrollbar_gutter(mouse_y, mouse_x):
            return False
        relative_y = mouse_y - track_y
        if thumb_pos <= relative_y < thumb_pos + thumb_size:
            self._scroll_drag_offset = relative_y - thumb_pos
        else:
            self._scroll_drag_offset = thumb_size // 2
        self._scroll_dragging = True
        self._drag_scrollbar_to(mouse_y)
        return True

    def _click(self, my, mx_pos, button=0, shift=False):
        if (
            button == 0
            and not self.confirm_open
            and not self.ctx_menu_open
            and self.dialog_mode is None
            and not self.delete_confirm_songs
            and not self.mp3_rename_old
            and not self.download_rename_path
            and not self.rename_active
        ):
            # The entire gutter is protected even when the list is too short
            # to need a thumb, so a near miss can never play an MP3.
            if self._in_scrollbar_gutter(my, mx_pos):
                self._start_scrollbar_drag(my, mx_pos)
                return
        if self.confirm_open:
            btn_row = self._confirm_y + self._confirm_h - 2
            if my == btn_row:
                for bx_start, bx_end, btn_idx in self._confirm_btn_regions:
                    if bx_start <= mx_pos < bx_end:
                        if btn_idx == 0:
                            self._confirm_yes()
                        else:
                            self._confirm_no()
                        return
            if (
                self._confirm_y <= my < self._confirm_y + self._confirm_h
                and self._confirm_x <= mx_pos < self._confirm_x + self._confirm_w
            ):
                return
            self._confirm_no()
            return

        if self.ctx_menu_open:
            # Use the stored draw coordinates for hit testing
            cy = self._ctx_draw_y
            cx = self._ctx_draw_x
            vis = self._ctx_draw_vis
            menu_w = self._ctx_draw_w
            if cy <= my <= cy + vis + 1 and cx <= mx_pos < cx + menu_w:
                row = my - cy - 1
                if 0 <= row < vis:
                    self.ctx_menu_sel = row
                    idx = self.ctx_menu_scroll + row
                    if 0 <= idx < len(self.ctx_menu_items):
                        if self.ctx_menu_items[idx][0] != "separator":
                            self._ctx_menu_execute()
                return
            else:
                self._close_ctx_menu()
                return

        if getattr(self, "_playlist_sidebar", False):
            try:
                ty, tx = self.win_tabs.getbegyx()
                th, tw = self.win_tabs.getmaxyx()
                if ty <= my < ty + th and tx <= mx_pos < tx + tw:
                    local_y = my - ty
                    local_x = mx_pos - tx
                    tab_idx = local_y - 2
                    if 0 <= tab_idx < len(self._tab_regions):
                        if button == 2 and self.tab_names[tab_idx] != "All":
                            self._open_ctx_menu_for_tab(
                                self.tab_names[tab_idx], my + 1, mx_pos
                            )
                            return
                        if tab_idx != self.active_tab:
                            if self.rename_active:
                                self._cancel_rename()
                            self.active_tab = tab_idx
                            self.scroll = 0
                            self._rebuild()
                            self._needs_redraw_tabs = True
                        return
                    if (
                        self._plus_btn_region
                        and local_y == getattr(self, "_sidebar_plus_y", -1)
                        and self._plus_btn_region[0]
                        <= local_x
                        < self._plus_btn_region[1]
                    ):
                        self._start_dialog("new_playlist")
                    return
            except Exception:
                pass

        was_search = self.focused == "search"
        if (
            was_search
            and not self.delete_confirm_songs
            and not self.download_rename_path
            and not self.mp3_rename_old
        ):
            try:
                iy = self.win_inp.getbegyx()[0]
                ih = self.win_inp.getmaxyx()[0]
                clicked_input = iy <= my < iy + ih
            except Exception:
                clicked_input = False
            if not clicked_input:
                self._unfocus_search()

        try:
            hy = self.win_hdr.getbegyx()[0]
            hh = self.win_hdr.getmaxyx()[0]
            if hy <= my < hy + hh:
                if self._youtube_back_region:
                    bx_start, bx_end, y_start, y_end = self._youtube_back_region
                    if hy + y_start <= my < hy + y_end and bx_start <= mx_pos < bx_end:
                        self._youtube_back_to_search()
                        return
                if self._clear_selection_region:
                    cs_start, cs_end, y_start, y_end = self._clear_selection_region
                    if hy + y_start <= my < hy + y_end and cs_start <= mx_pos < cs_end:
                        self._exit_current_youtube_preview()
                        return
                if self._mode_region:
                    mx_start, mx_end, y_start, y_end = self._mode_region
                    if hy + y_start <= my < hy + y_end and mx_start <= mx_pos < mx_end:
                        if self.youtube_preview_enabled:
                            self._start_youtube_download()
                        elif self.apple_radio_enabled:
                            result = self._radio_download_result()
                            if result:
                                self._start_youtube_download(result)
                            else:
                                self.status_msg = "No radio song title to download"
                                self._needs_redraw_hdr = True
                                self.dirty = True
                        else:
                            self._switch_play_mode()
                        return
                if self._mode_label_region:
                    mx_start, mx_end, y_start, y_end = self._mode_label_region
                    if hy + y_start <= my < hy + y_end and mx_start <= mx_pos < mx_end:
                        return
                if self._weights_region:
                    wx_start, wx_end, y_start, y_end = self._weights_region
                    if hy + y_start <= my < hy + y_end and wx_start <= mx_pos < wx_end:
                        self.show_shuffle_weights = not self.show_shuffle_weights
                        self.scroll = 0
                        if self.show_shuffle_weights:
                            self._shuffle_weight_rows_for_view()
                        else:
                            self._apply_search_filter()
                        self._needs_redraw_hdr = True
                        self._needs_redraw_lst = True
                        self.dirty = True
                        return
                if self._apple_radio_region:
                    ar_start, ar_end, y_start, y_end = self._apple_radio_region
                    if hy + y_start <= my < hy + y_end and ar_start <= mx_pos < ar_end:
                        self._toggle_apple_radio_mode()
                        return
                if self._vol_down_region:
                    vx_start, vx_end, y_start, y_end = self._vol_down_region
                    if hy + y_start <= my < hy + y_end and vx_start <= mx_pos < vx_end:
                        self._adjust_volume(-1)
                        return
                if self._vol_up_region:
                    vx_start, vx_end, y_start, y_end = self._vol_up_region
                    if hy + y_start <= my < hy + y_end and vx_start <= mx_pos < vx_end:
                        self._adjust_volume(1)
                        return
        except Exception:
            pass

        if not (
            self.apple_radio_enabled
            or self.youtube_preview_enabled
            or getattr(self, "_playlist_sidebar", False)
        ):
            try:
                ty = self.win_tabs.getbegyx()[0]
                th = self.win_tabs.getmaxyx()[0]
                if ty <= my < ty + th and my < ty + 3:
                    for x_start, x_end, tab_idx in self._tab_regions:
                        if x_start <= mx_pos < x_end:
                            if button == 2 and self.tab_names[tab_idx] != "All":
                                self._open_ctx_menu_for_tab(
                                    self.tab_names[tab_idx], my + 1, mx_pos
                                )
                                return
                            if tab_idx != self.active_tab:
                                if self.rename_active:
                                    self._cancel_rename()
                                self.active_tab = tab_idx
                                self.scroll = 0
                                self._rebuild()
                                self._needs_redraw_tabs = True
                            return
                    if self._plus_btn_region:
                        px_start, px_end = self._plus_btn_region
                        if px_start <= mx_pos < px_end:
                            self._start_dialog("new_playlist")
                            return
                    return
            except Exception:
                pass

        try:
            iy = self.win_inp.getbegyx()[0]
            ih = self.win_inp.getmaxyx()[0]
            if iy <= my < iy + ih:
                if self.dialog_mode is not None:
                    local_y = my - iy
                    cancel = self._dialog_cancel_region
                    if (
                        cancel
                        and local_y == cancel[2]
                        and cancel[0] <= mx_pos < cancel[1]
                    ):
                        self._cancel_dialog()
                        return
                    iw = self.win_inp.getmaxyx()[1]
                    label = " New playlist name: "
                    cancel_x = max(1, iw - len("[Cancel]") - 2)
                    if 2 + len(label) + 2 >= cancel_x:
                        label = " Name: "
                    field_x = 2 + len(label)
                    if local_y == 1 and field_x <= mx_pos < cancel_x - 1:
                        field_w = max(1, cancel_x - field_x - 1)
                        start = (
                            max(0, self.dialog_cursor - field_w + 1)
                            if len(self.dialog_buf) > field_w
                            else 0
                        )
                        self.dialog_cursor = min(
                            len(self.dialog_buf), start + mx_pos - field_x
                        )
                        self._needs_redraw_inp = True
                        self.dirty = True
                    return
                if self.mp3_rename_old or self.rename_active:
                    local_y = my - iy
                    cancel = self._rename_cancel_region
                    if (
                        cancel
                        and local_y == cancel[2]
                        and cancel[0] <= mx_pos < cancel[1]
                    ):
                        if self.mp3_rename_old:
                            self._cancel_mp3_rename()
                        else:
                            self._cancel_rename()
                        return
                    iw = self.win_inp.getmaxyx()[1]
                    label = " Rename MP3: " if self.mp3_rename_old else " Rename playlist: "
                    cancel_x = max(1, iw - len("[Cancel]") - 2)
                    if 2 + len(label) + 2 >= cancel_x:
                        label = " Name: "
                    field_x = 2 + len(label)
                    if local_y == 1 and field_x <= mx_pos < cancel_x - 1:
                        field_w = max(1, cancel_x - field_x - 1)
                        buf = self.mp3_rename_buf if self.mp3_rename_old else self.rename_buf
                        cursor = self.mp3_rename_cursor if self.mp3_rename_old else self.rename_cursor
                        start = max(0, cursor - field_w + 1) if len(buf) > field_w else 0
                        new_cursor = min(len(buf), start + mx_pos - field_x)
                        if self.mp3_rename_old:
                            self.mp3_rename_cursor = new_cursor
                        else:
                            self.rename_cursor = new_cursor
                        self._needs_redraw_inp = True
                        self.dirty = True
                    return
                if self.delete_confirm_songs:
                    self.focused = "search"
                    self.dirty = True
                    self._needs_redraw_inp = True
                    self._update_timeout()
                    return
                if self.mp3_rename_old:
                    if self.focused != "search":
                        self.focused = "search"
                        self._update_timeout()
                    iw = self.win_inp.getmaxyx()[1]
                    label = " Rename MP3: "
                    click_pos = mx_pos - (2 + len(label))
                    if click_pos >= 0:
                        max_text_w = max(1, iw - 4 - len(label))
                        if len(self.mp3_rename_buf) > max_text_w:
                            start = max(
                                0, self.mp3_rename_cursor - max_text_w + 1
                            )
                            self.mp3_rename_cursor = min(
                                start + click_pos, len(self.mp3_rename_buf)
                            )
                        else:
                            self.mp3_rename_cursor = min(
                                click_pos, len(self.mp3_rename_buf)
                            )
                    self.dirty = True
                    self._needs_redraw_inp = True
                    return
                if self.download_rename_path:
                    if self.focused != "search":
                        self.focused = "search"
                        self._update_timeout()
                    iw = self.win_inp.getmaxyx()[1]
                    label = " Name: "
                    click_pos = mx_pos - (2 + len(label))
                    if click_pos >= 0:
                        max_text_w = max(1, iw - 4 - len(label))
                        if len(self.download_rename_buf) > max_text_w:
                            start = max(
                                0, self.download_rename_cursor - max_text_w + 1
                            )
                            self.download_rename_cursor = min(
                                start + click_pos, len(self.download_rename_buf)
                            )
                        else:
                            self.download_rename_cursor = min(
                                click_pos, len(self.download_rename_buf)
                            )
                    self.dirty = True
                    self._needs_redraw_inp = True
                    return
                if self._yt_preview_toggle_region:
                    tx_start, tx_end, ty_start, ty_end = self._yt_preview_toggle_region
                    if (
                        iy + ty_start <= my < iy + ty_end
                        and tx_start <= mx_pos < tx_end
                    ):
                        if self.youtube_preview_enabled:
                            self._exit_youtube_preview_mode()
                        else:
                            if self.apple_radio_enabled:
                                self._exit_apple_radio_mode()
                            self.youtube_preview_enabled = True
                            self.status_msg = "YouTube preview search enabled"
                            self._rebuild()
                            self._layout()
                        self._needs_redraw_hdr = True
                        self._needs_redraw_tabs = True
                        self._needs_redraw_bar = True
                        self._needs_redraw_lst = True
                        self._needs_redraw_inp = True
                        self.dirty = True
                        self._update_timeout()
                        return
                if self.focused != "search":
                    self.focused = "search"
                    self.dirty = True
                    self._needs_redraw_inp = True
                    self._needs_redraw_lst = True
                    self._update_timeout()
                iw = self.win_inp.getmaxyx()[1]
                label = " Search: "
                click_pos = mx_pos - (2 + len(label))
                if click_pos >= 0:
                    toggle_w = len("[YT Preview]") + 1
                    max_text_w = max(1, iw - 4 - len(label) - toggle_w)
                    if len(self.search) > max_text_w:
                        start = max(0, self.search_cursor - max_text_w + 1)
                        self.search_cursor = min(
                            start + click_pos, len(self.search)
                        )
                    else:
                        self.search_cursor = min(click_pos, len(self.search))
                    self._needs_redraw_inp = True
                return
        except Exception:
            pass

        try:
            ly, lx = self.win_lst.getbegyx()
            lh = self.win_lst.getmaxyx()[0]
            list_mx = mx_pos - lx
            if ly < my < ly + lh - 1:
                if my == ly + 1:
                    for x_start, x_end, sort_mode in self._sort_col_regions:
                        if x_start <= list_mx < x_end:
                            if self.youtube_preview_enabled:
                                if self.youtube_sort_mode == sort_mode:
                                    self.youtube_sort_reverse = not self.youtube_sort_reverse
                                else:
                                    self.youtube_sort_mode = sort_mode
                                    self.youtube_sort_reverse = sort_mode != "title"
                                self._sort_youtube_results()
                            elif self.show_shuffle_weights:
                                if self.weight_sort_mode == sort_mode:
                                    self.weight_sort_reverse = (
                                        not self.weight_sort_reverse
                                    )
                                else:
                                    self.weight_sort_mode = sort_mode
                                    self.weight_sort_reverse = sort_mode != "name"
                                self._shuffle_weight_rows_for_view()
                            else:
                                if self.sort_mode == sort_mode:
                                    self.sort_reverse = not self.sort_reverse
                                else:
                                    self.sort_mode = sort_mode
                                    self.sort_reverse = sort_mode != "name"
                                self._rebuild()
                                self._rebuild_play_pool()
                            self._needs_redraw_lst = True
                            self.dirty = True
                            return
                    return
                if was_search:
                    self.focused = "list"
                    self.dirty = True
                    self._needs_redraw_inp = True
                    self._needs_redraw_lst = True
                if self.focused != "list":
                    self.focused = "list"
                    self._needs_redraw_inp = True
                    self._needs_redraw_lst = True
                row = my - ly - 2
                idx = self.scroll + row
                if self.apple_radio_enabled:
                    if 0 <= idx < len(APPLE_RADIO_STATIONS):
                        self._play_apple_radio_station(
                            APPLE_RADIO_STATIONS[idx]
                        )
                    self.dirty = True
                    return
                if self.youtube_preview_enabled:
                    if 0 <= idx < len(self.youtube_results):
                        result = self.youtube_results[idx]
                        if self._is_youtube_channel_result(result):
                            self._open_youtube_channel(result)
                        else:
                            self._start_youtube_preview(result)
                    self.dirty = True
                    return
                if 0 <= idx < len(self.songs):
                    song_name = self.songs[idx]
                    row_click_end = getattr(self, "_row_click_end", None)
                    if row_click_end is not None and list_mx >= row_click_end:
                        # Blank table space after the Last column is not part
                        # of the song row's click target.
                        return
                    if self._num_col_region:
                        nx_start, nx_end = self._num_col_region
                        if nx_start <= list_mx < nx_end and button == 0:
                            self._select_song_number(song_name, idx, shift=shift)
                            return
                    if button == 2:
                        self._open_ctx_menu_for_song(song_name, my, mx_pos)
                    else:
                        self.play(song_name)
                self.dirty = True
                return
        except Exception:
            pass

        if self.focused != "list":
            self.focused = "list"
            self.dirty = True
            self._needs_redraw_inp = True
            self._needs_redraw_lst = True

    def _try_read_sgr(self):
        c = self.stdscr.getch()
        if c != ord("["):
            if c != -1:
                curses.ungetch(c)
            return False
        c = self.stdscr.getch()
        if c == ord("2"):
            seq = [c]
            for _ in range(4):
                c = self.stdscr.getch()
                if c == -1:
                    return False
                seq.append(c)
                if c == ord("~"):
                    break
            if seq == [ord("2"), ord("0"), ord("0"), ord("~")]:
                return self._read_bracketed_paste()
            for value in reversed(seq):
                curses.ungetch(value)
            return False
        if c != ord("<"):
            if c != -1:
                curses.ungetch(c)
            return False
        buf = []
        is_press = False
        ok = False
        for _ in range(25):
            c = self.stdscr.getch()
            if c == -1:
                return False
            if c == 77 or c == 109:
                is_press = c == 77
                ok = True
                break
            buf.append(c)
        if not ok:
            return False
        try:
            parts = bytes(buf).decode("ascii").split(";")
            if len(parts) < 3:
                return False
            cb = int(parts[0])
            cx = int(parts[1]) - 1
            cy = int(parts[2]) - 1
        except (ValueError, IndexError, UnicodeDecodeError):
            return False
        shift = bool(cb & 4)
        motion = bool(cb & 32)
        if not is_press:
            self._scroll_dragging = False
            return True
        if motion:
            if getattr(self, "_scroll_dragging", False):
                self._drag_scrollbar_to(cy)
            return True
        base_cb = cb & ~28
        if base_cb == 64:
            if self.ctx_menu_open:
                self._ctx_scroll_up()
            else:
                self._scroll_up()
        elif base_cb == 65:
            if self.ctx_menu_open:
                self._ctx_scroll_down()
            else:
                self._scroll_down()
        elif base_cb == 0:
            self._click(cy, cx, button=0, shift=shift)
        elif base_cb == 2:
            self._click(cy, cx, button=2, shift=shift)
        return True

    def _active_text_field(self):
        if self.mp3_rename_old:
            return "mp3_rename"
        if self.download_rename_path:
            return "download_rename"
        if self.rename_active:
            return "tab_rename"
        if self.dialog_mode is not None:
            return "dialog"
        if self.focused == "search":
            return "search"
        return ""

    def _read_bracketed_paste(self):
        field = self._active_text_field()
        if not field:
            return True
        end_seq = [27, ord("["), ord("2"), ord("0"), ord("1"), ord("~")]
        data = []
        tail = []
        old_timeout = self._is_active or UI_REFRESH_MS_IDLE
        try:
            self.stdscr.timeout(20)
            misses = 0
            while misses < 25 and len(data) < 5000:
                c = self.stdscr.getch()
                if c == -1:
                    misses += 1
                    continue
                misses = 0
                data.append(c)
                tail.append(c)
                if len(tail) > len(end_seq):
                    tail.pop(0)
                if tail == end_seq:
                    data = data[: -len(end_seq)]
                    break
        finally:
            self.stdscr.timeout(old_timeout)
        text = "".join(chr(c) for c in data if c >= 0)
        if text:
            original, cursor, max_len = self._text_field(field)
            text = self._clean_paste_text(text)
            room = max_len - len(original)
            if room > 0 and text:
                text = text[:room]
                self._set_text_field(
                    field,
                    original[:cursor] + text + original[cursor:],
                    cursor + len(text),
                )
        return True

    @staticmethod
    def _fmt_time(ms):
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"

    @staticmethod
    def _fmt_yt_duration(seconds):
        try:
            seconds = int(seconds or 0)
        except (TypeError, ValueError):
            seconds = 0
        if seconds <= 0:
            return "--:--"
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    @staticmethod
    def _fmt_listen_hours(hours):
        return f"{max(0.0, float(hours)):.2f}h"

    def _tab_song_names(self):
        tab_name = self._current_tab_name()
        if tab_name == "All":
            return list(self.all_songs)
        return self._playlist_song_names(tab_name)

    def _library_stats_label(self):
        songs = self._tab_song_names()
        listen_hours = 0.0
        duration_ms = 0
        total_plays = 0
        for name in songs:
            total_plays += self._plays_cache.get(name, 0)
            listen_hours += self._total_listen_hours(name)
            duration_ms += max(0, self._cached_duration_ms(name))
        duration_hours = duration_ms / 3600000
        return (
            f"listen: {listen_hours:.2f}h | plays: {total_plays} | "
            f"duration: {duration_hours:.2f}h"
        )

    def _safe(self, win, y, x, text, attr=0):
        try:
            win.addstr(y, x, text, attr)
        except curses.error:
            pass

    @staticmethod
    def _truncate(text, width):
        width = max(0, int(width))
        text = str(text)
        if cell_len(text) <= width:
            return text
        if width <= 3:
            return "." * width
        # Terminal glyphs are not uniformly one cell wide. Crop by rendered
        # cell width so emoji, CJK, and combining marks don't get hard-cut or
        # make a supposedly fitted table overflow.
        return set_cell_size(text, width - 3).rstrip() + "..."

    @staticmethod
    def _truncate_song_name(text, width):
        """Shorten a filename while keeping a leading ``[artist]`` tag whole."""
        text = str(text)
        width = max(0, int(width))
        if cell_len(text) <= width:
            return text
        match = re.match(r"^(\[[^\]]+\]\s*)(.*)$", text)
        if not match:
            return Player._truncate(text, width)
        artist_tag, title = match.groups()
        tag_width = cell_len(artist_tag)
        if tag_width >= width:
            # The column cannot contain anything beyond the tag. Showing the
            # complete tag is more useful than turning it into "[Art...".
            return artist_tag.rstrip()
        return artist_tag + Player._truncate(title, width - tag_width)

    @staticmethod
    def _without_artist_tag(text):
        """Return a filename label without its leading ``[artist]`` tag."""
        text = str(text)
        match = re.match(r"^\[[^\]]+\]\s*(.*)$", text)
        return match.group(1) if match else text

    def _btn(self, win, y, x, text, selected=False):
        s = f"[{text}]"
        attr = curses.A_REVERSE if selected else curses.A_NORMAL
        self._safe(win, y, x, s, attr)
        return len(s)

    def _outline_btn(self, win, y, x, text, selected=False):
        label = f" {text} "
        border = curses.A_BOLD
        fill = curses.A_REVERSE | curses.A_BOLD if selected else curses.A_NORMAL
        width = len(label) + 2
        self._safe(win, y, x, "┌" + "─" * len(label) + "┐", border)
        self._safe(win, y + 1, x, "│", border)
        self._safe(win, y + 1, x + 1, label, fill)
        self._safe(win, y + 1, x + width - 1, "│", border)
        self._safe(win, y + 2, x, "└" + "─" * len(label) + "┘", border)
        return width

    def _merged_tab(self, win, y, x, text, selected=False, first=False):
        """Draw a bottom-open tab that shares edges with its neighbours."""
        label = f" {text} "
        width = len(label) + 2
        left_corner = "┌" if first else "┬"
        self._safe(win, y, x, left_corner + "─" * len(label) + "┐", curses.A_NORMAL)
        self._safe(win, y + 1, x, "│", curses.A_DIM)
        fill = curses.A_REVERSE if selected else curses.A_NORMAL
        self._safe(win, y + 1, x + 1, label, fill)
        self._safe(win, y + 1, x + width - 1, "│", curses.A_DIM)
        return width

    def _draw_playlist_sidebar(self):
        """Draw playlists as a vertical navigation panel beside the library."""
        win = self.win_tabs
        win.erase()
        th, tw = win.getmaxyx()
        win.box()
        title = " PLAYLISTS "
        self._safe(win, 0, 2, title[: max(0, tw - 4)], curses.A_BOLD)
        self._tab_regions = []
        self._plus_btn_region = None
        self._active_tab_opening = None
        self._sidebar_save_region = None
        self._sidebar_cancel_region = None

        reserved_rows = 5
        max_rows = max(0, th - reserved_rows)
        for i, tname in enumerate(self.tab_names[:max_rows]):
            y = i + 2
            marker = "›" if i == self.active_tab else " "
            label = f" {marker} {self._truncate(tname, max(1, tw - 7))}"
            attr = curses.A_REVERSE if i == self.active_tab else curses.A_NORMAL
            self._safe(win, y, 1, label.ljust(max(1, tw - 2)), attr)
            self._tab_regions.append((1, max(1, tw - 1), i))

        plus_y = min(th - 2, len(self._tab_regions) + 3)
        if plus_y > 1:
            plus_label = " +  New playlist"
            self._safe(win, plus_y, 1, plus_label[: max(1, tw - 2)], curses.A_DIM)
            self._plus_btn_region = (1, min(tw - 1, len(plus_label) + 1))
            self._sidebar_plus_y = plus_y

        curses.curs_set(0)

        win.noutrefresh()

    def draw(self):
        if self._is_active:
            now = time.monotonic()
            if now - self._last_draw_time >= DURATION_REDRAW_S:
                self._last_draw_time = now
                self._needs_redraw_bar = True
                self.dirty = True

        # Z-INDEX FIX: If overlays are open, force them to redraw every frame
        # so timed subwindow refreshes (like the duration bar) don't paint over them.
        if self.ctx_menu_open:
            self._needs_redraw_ctx = True
        if self.confirm_open:
            self._needs_redraw_confirm = True

        if not self.dirty:
            return
        self.dirty = False

        try:
            ww = self.stdscr.getmaxyx()[1]
        except Exception:
            return

        BOLD = curses.A_BOLD
        DIM = curses.A_DIM
        REV = curses.A_REVERSE
        UL = curses.A_UNDERLINE
        N = curses.A_NORMAL
        RED = curses.color_pair(2)

        # ── Header ──
        if self._needs_redraw_hdr:
            self._needs_redraw_hdr = False
            self._mode_region = None
            self._mode_label_region = None
            self._weights_region = None
            self._apple_radio_region = None
            self._sort_region = None
            self._vol_down_region = None
            self._vol_up_region = None
            self._clear_selection_region = None
            self._youtube_back_region = None
            try:
                self.win_hdr.erase()

                # Row 0: NOW PLAYING
                self._safe(self.win_hdr, 0, 3, "NOW PLAYING", BOLD | UL)
                icon = "⏸" if self.paused else "▶"
                display_current = self.current
                if self.apple_radio_enabled:
                    display_current = (
                        self.apple_radio_music_now
                        or self.apple_radio_now
                        or "Apple Music Radio"
                    )
                    icon = "▶"
                np_str = f"  {icon}  {display_current}"
                max_np = ww - 15
                from_label = self.play_tab
                if self.apple_radio_enabled:
                    from_label = self.apple_radio_active or "Apple Music"
                elif self.current in ("None", "Loading..."):
                    from_label = self._current_tab_name()
                if self.apple_radio_enabled:
                    from_str = f"from: {from_label}"
                else:
                    from_str = f"{self._library_stats_label()} | from: {from_label}"
                from_x = ww - len(from_str) - 3
                if from_x > 15:
                    self._safe(self.win_hdr, 0, from_x, from_str, DIM)
                    max_np = from_x - 15
                self._safe(
                    self.win_hdr,
                    0,
                    14,
                    self._truncate(np_str, max(1, max_np)),
                    BOLD,
                )

                # Row 1: thin separator
                self._safe(self.win_hdr, 1, 3, "─" * (ww - 6), DIM)
                if self.status_msg:
                    self._safe(
                        self.win_hdr,
                        2,
                        3,
                        self._truncate(self.status_msg, max(1, ww - 6)),
                        DIM,
                    )

                # Row 4: VOL ... MODE
                self._safe(self.win_hdr, 4, 3, "VOL", BOLD)
                x = 10
                vdn_w = self._outline_btn(self.win_hdr, 3, x, "−")
                self._vol_down_region = (x, x + vdn_w, 3, 6)
                x += vdn_w + 1

                vp = int(round(self.vol * 10))
                vbar = "█" * vp + "·" * (10 - vp)
                self._safe(self.win_hdr, 4, x, vbar, N)
                x += 11
                self._safe(self.win_hdr, 4, x, f"{int(self.vol * 100):>3}%", N)
                x += 5
                vup_w = self._outline_btn(self.win_hdr, 3, x, "+")
                self._vol_up_region = (x, x + vup_w, 3, 6)
                x += vup_w + 3
                if self.youtube_preview_enabled or self.apple_radio_enabled:
                    mode_txt = "DOWNLOAD"
                    mbx = x
                elif x + 12 < ww:
                    self._safe(self.win_hdr, 4, x, "MODE", BOLD)
                    self._mode_label_region = (x, x + 4, 4, 5)
                    mode_txt = self.play_mode.upper()
                    mbx = x + 6
                else:
                    mode_txt = ""
                    mbx = x
                if mode_txt:
                    mode_w = self._outline_btn(self.win_hdr, 3, mbx, mode_txt)
                    self._mode_region = (mbx, mbx + mode_w, 3, 6)
                    x = mbx + mode_w + 3
                    self._youtube_back_region = None
                    if (
                        self.song_switch_locked
                        and not self.youtube_preview_enabled
                        and not self.apple_radio_enabled
                        and x + len("LOCKED") < ww - 2
                    ):
                        self._safe(self.win_hdr, 4, x, "LOCKED", REV | BOLD)
                        x += len("LOCKED") + 3
                    if (
                        self.youtube_preview_enabled
                        and self._youtube_view == "channel"
                        and x + len("BACK") + 2 < ww
                    ):
                        back_w = self._outline_btn(self.win_hdr, 3, x, "BACK")
                        self._youtube_back_region = (x, x + back_w, 3, 6)
                        x += back_w + 3
                    action_label = "EXIT PREVIEW"
                    if (
                        self.youtube_preview_enabled
                        and x + len(action_label) + 2 < ww
                    ):
                        clear_w = self._outline_btn(
                            self.win_hdr, 3, x, action_label
                        )
                        self._clear_selection_region = (x, x + clear_w, 3, 6)
                        x += clear_w + 3
                    weights_label = "weights"
                    if (
                        not self.youtube_preview_enabled
                        and not self.apple_radio_enabled
                        and x + len(weights_label) < ww - 2
                    ):
                        attr = (REV | BOLD) if self.show_shuffle_weights else DIM | UL
                        self._safe(self.win_hdr, 4, x, weights_label, attr)
                        self._weights_region = (x, x + len(weights_label), 4, 5)
                        x += len(weights_label) + 3
                    radio_label = "radio"
                    if x + len(radio_label) < ww - 2:
                        attr = (REV | BOLD) if self.apple_radio_enabled else DIM | UL
                        self._safe(self.win_hdr, 4, x, radio_label, attr)
                        self._apple_radio_region = (
                            x,
                            x + len(radio_label),
                            4,
                            5,
                        )

            except curses.error:
                pass
            self.win_hdr.noutrefresh()

        # ── Tabs ──
        if self._needs_redraw_tabs:
            self._needs_redraw_tabs = False
            if self.apple_radio_enabled or self.youtube_preview_enabled:
                self._tab_regions = []
                self._plus_btn_region = None
                self._needs_redraw_lst = True
                self.dirty = True
            elif getattr(self, "_playlist_sidebar", False):
                try:
                    self._tab_row_y = self.win_tabs.getbegyx()[0]
                    self._draw_playlist_sidebar()
                except curses.error:
                    pass
            else:
                try:
                    self.win_tabs.erase()
                    tw = self.win_tabs.getmaxyx()[1]
                    self._tab_row_y = self.win_tabs.getbegyx()[0]
                    self._tab_regions = []
                    self._plus_btn_region = None
                    self._active_tab_opening = None
                    curses.curs_set(0)
                    # Keep the restored whitespace above the navigation, then
                    # let the open bottoms land directly on the list border.
                    tab_y = 1
                    x = 2
                    for i, tname in enumerate(self.tab_names):
                        btn_w = len(tname) + 4
                        if x + btn_w > tw:
                            self._safe(self.win_tabs, tab_y + 1, x, "…", N)
                            break
                        if i == self.active_tab:
                            self._merged_tab(self.win_tabs, tab_y, x, tname, True, i == 0)
                            self._active_tab_opening = (x, x + btn_w - 1)
                        else:
                            self._merged_tab(self.win_tabs, tab_y, x, tname, False, i == 0)
                        self._tab_regions.append((x, x + btn_w, i))
                        x += btn_w - 1
                    if x + 3 < tw:
                        plus_x = x
                        plus_w = self._merged_tab(
                            self.win_tabs, tab_y, plus_x, "+", False, not self.tab_names
                        )
                        self._plus_btn_region = (plus_x, plus_x + plus_w)
                except curses.error:
                    pass
                self.win_tabs.noutrefresh()
                self._needs_redraw_lst = True

        # ── Duration bar ──
        if self._needs_redraw_bar:
            self._needs_redraw_bar = False
            if self.apple_radio_enabled:
                self._needs_redraw_lst = True
                self.dirty = True
            else:
                try:
                    self.win_bar.erase()
                    cur_ms, tot_ms = self._current_pos_in_song()
                    time_label = f"{self._fmt_time(cur_ms)} / {self._fmt_time(tot_ms)}"
                    bar_area = max(4, ww - len(time_label) - 6)
                    filled = int(bar_area * cur_ms / tot_ms) if tot_ms > 0 else 0
                    if filled > bar_area:
                        filled = bar_area
                    self._safe(self.win_bar, 0, 1, f" {time_label}  ", DIM)
                    bx = len(time_label) + 4
                    self._safe(self.win_bar, 0, bx, "━" * filled, BOLD)
                    self._safe(
                        self.win_bar,
                        0,
                        bx + filled,
                        "─" * (bar_area - filled),
                        DIM,
                    )
                except curses.error:
                    pass
                self.win_bar.noutrefresh()

        # ── List ──
        if self._needs_redraw_lst:
            self._needs_redraw_lst = False
            try:
                self.win_lst.erase()
                lh, lw = self.win_lst.getmaxyx()
                self._num_col_region = None
                self._row_click_end = None
                vis = max(0, lh - 3)
                shuffle_weight_rows = []
                if (
                    self.show_shuffle_weights
                    and not self.youtube_preview_enabled
                    and not self.apple_radio_enabled
                ):
                    shuffle_weight_rows = self._shuffle_weight_rows_for_view()
                total = (
                    len(APPLE_RADIO_STATIONS)
                    if self.apple_radio_enabled
                    else len(self.youtube_results)
                    if self.youtube_preview_enabled
                    else len(shuffle_weight_rows)
                    if self.show_shuffle_weights
                    else len(self.songs)
                )
                mss = max(0, total - vis)
                if self.scroll > mss:
                    self.scroll = mss
                elif self.scroll < 0:
                    self.scroll = 0
                if self.focused == "list":
                    self.win_lst.attron(BOLD)
                    self.win_lst.box()
                    self.win_lst.attroff(BOLD)
                else:
                    self.win_lst.box()
                if getattr(self, "_playlist_sidebar", False):
                    # Both panels overlap by one column, forming one shared
                    # divider instead of two borders drawn side by side.
                    self._safe(self.win_lst, 0, 0, "┬", N)
                    self._safe(self.win_lst, lh - 1, 0, "┴", N)
                # The selected playlist is one open tab flowing into this panel.
                opening = getattr(self, "_active_tab_opening", None)
                if opening and not self.rename_active:
                    ox1, ox2 = opening
                    ox1 = max(1, min(ox1, lw - 2))
                    ox2 = max(ox1 + 1, min(ox2, lw - 1))
                    # Join the panel rule to the active tab with lightweight,
                    # inward-facing corners. Bold box glyphs can visually hang
                    # below the rule in some terminal fonts.
                    self._safe(self.win_lst, 0, ox1, "┘", N)
                    self._safe(
                        self.win_lst,
                        0,
                        ox1 + 1,
                        " " * max(0, ox2 - ox1 - 1),
                        N,
                    )
                    self._safe(self.win_lst, 0, ox2, "└", N)
                # Reserve a three-column gutter at the right. Content and row
                # click targets stop before it; the track sits in its center.
                scrollbar_col = max(1, lw - 4)
                scrollbar_track_col = max(1, lw - 3)
                max_label = max(0, scrollbar_col - 1)
                cw = self.col_width
                if max_label > 0:
                    self._sort_col_regions = []
                    self._num_col_region = None
                    if self.apple_radio_enabled:
                        left_pad = 2
                        num_w = max(3, len(str(max(total, 1))))
                        col_gap = 4 if max_label >= 54 else 2
                        fixed_w = left_pad + num_w + col_gap
                        station_w = max(1, max_label - fixed_w)
                        num_x = 1 + left_pad
                        station_x = num_x + num_w + col_gap
                        headers = [
                            (num_x, num_w, "#", True),
                            (station_x, station_w, "Station", False),
                        ]
                        for x, width, title, align_right in headers:
                            if x >= scrollbar_col or width <= 0:
                                continue
                            title = self._truncate(title, width)
                            text = title.rjust(width) if align_right else title.ljust(width)
                            clip_w = max(0, min(width, scrollbar_col - x))
                            self._safe(self.win_lst, 1, x, text[:clip_w], BOLD)
                        for i in range(vis):
                            idx = self.scroll + i
                            if idx >= total:
                                break
                            station = APPLE_RADIO_STATIONS[idx]
                            name = station.get("name", "")
                            active = name == self.apple_radio_active
                            attr = REV | BOLD if active else N
                            row_num = str(idx + 1)
                            row_y = i + 2
                            self._safe(self.win_lst, row_y, 1, " " * max_label, attr)
                            station_text = self._truncate(name, station_w).ljust(
                                station_w
                            )
                            cells = [
                                (num_x, num_w, row_num.rjust(num_w)),
                                (station_x, station_w, station_text),
                            ]
                            for x, width, text in cells:
                                if x >= scrollbar_col or width <= 0:
                                    continue
                                clip_w = max(0, min(width, scrollbar_col - x))
                                self._safe(
                                    self.win_lst,
                                    row_y,
                                    x,
                                    str(text)[:clip_w],
                                    attr,
                                )
                    elif self.show_shuffle_weights and not self.youtube_preview_enabled:
                        left_pad = 2
                        select_w = 1
                        num_w = max(3, len(str(max(total, 1))))
                        plays_w = 7
                        z_w = 8
                        weight_w = 8
                        chance_w = 8
                        col_gap = 4
                        fixed_w = (
                            select_w
                            + left_pad
                            + num_w
                            + plays_w
                            + z_w
                            + weight_w
                            + chance_w
                            + (col_gap * 5)
                            + 1
                        )
                        available_w = max(1, max_label - left_pad)
                        name_w = min(42, max(1, available_w - fixed_w))
                        select_x = 1 + left_pad
                        num_x = select_x + select_w + 1
                        name_x = num_x + num_w + col_gap
                        plays_x = name_x + name_w + col_gap
                        z_x = plays_x + plays_w + col_gap
                        weight_x = z_x + z_w + col_gap
                        chance_x = weight_x + weight_w + col_gap
                        self._num_col_region = (
                            1,
                            min(num_x, scrollbar_col),
                        )
                        headers = [
                            (None, select_x, select_w, "○", False),
                            (None, num_x, num_w, "#", True),
                            ("name", name_x, name_w, "Name", False),
                            ("plays", plays_x, plays_w, "Plays", True),
                            ("z", z_x, z_w, "Z", True),
                            ("weight", weight_x, weight_w, "Weight", True),
                            ("chance", chance_x, chance_w, "Chance", True),
                        ]
                        for mode, x, width, title, align_right in headers:
                            if x >= scrollbar_col:
                                continue
                            if mode is not None and self.weight_sort_mode == mode:
                                arrow = " ↓" if self.weight_sort_reverse else " ↑"
                                title = f"{title}{arrow}"
                            if (
                                mode is not None
                                and len(title) > width
                                and self.weight_sort_mode == mode
                            ):
                                title = f"{title[: max(1, width - 2)]}{arrow}"
                            text = title.rjust(width) if align_right else title.ljust(width)
                            text = text[: max(0, min(width, scrollbar_col - x))]
                            self._safe(self.win_lst, 1, x, text, BOLD)
                            if mode is not None:
                                self._sort_col_regions.append(
                                    (x, min(x + width, scrollbar_col), mode)
                                )
                        for i in range(vis):
                            idx = self.scroll + i
                            if idx >= total:
                                break
                            row = shuffle_weight_rows[idx]
                            name = row["name"]
                            song_num = idx + 1
                            is_selected = name in self.selected_songs
                            select_mark = "●" if is_selected else "○"
                            display_name = self._truncate_song_name(name, name_w).ljust(name_w)
                            attr = REV | BOLD if name == self.current else N
                            cells = [
                                (select_x, select_w, select_mark, attr),
                                (num_x, num_w, str(song_num).rjust(num_w), attr),
                                (name_x, name_w, display_name, attr),
                                (
                                    plays_x,
                                    plays_w,
                                    str(row.get("plays", 0)).rjust(plays_w),
                                    attr,
                                ),
                                (
                                    z_x,
                                    z_w,
                                    f"{row.get('z', 0.0):+.2f}".rjust(z_w),
                                    attr,
                                ),
                                (
                                    weight_x,
                                    weight_w,
                                    f"{row.get('weight', 0.0):.3f}".rjust(weight_w),
                                    attr,
                                ),
                                (
                                    chance_x,
                                    chance_w,
                                    f"{row.get('probability', 0.0) * 100:5.1f}%".rjust(
                                        chance_w
                                    ),
                                    attr,
                                ),
                            ]
                            row_y = i + 2
                            self._safe(self.win_lst, row_y, 1, " " * max_label, attr)
                            for x, width, text, cell_attr in cells:
                                if x >= scrollbar_col or width <= 0:
                                    continue
                                clip_w = max(0, min(width, scrollbar_col - x))
                                self._safe(
                                    self.win_lst,
                                    row_y,
                                    x,
                                    str(text)[:clip_w],
                                    cell_attr,
                                )
                    elif self.youtube_preview_enabled:
                        left_pad = 2
                        num_w = max(3, len(str(max(total, 1))))
                        col_gap = 4 if max_label >= 54 else 2
                        views_w = 10 if max_label >= 38 else 0
                        likes_w = 10 if max_label >= 52 else 0
                        fixed_w = left_pad + num_w + col_gap
                        if views_w:
                            fixed_w += views_w + col_gap
                        if likes_w:
                            fixed_w += likes_w + col_gap
                        title_w = max(1, max_label - fixed_w)
                        if title_w < 8 and likes_w:
                            fixed_w -= likes_w + col_gap
                            likes_w = 0
                            title_w = max(1, max_label - fixed_w)
                        if title_w < 8 and views_w:
                            fixed_w -= views_w + col_gap
                            views_w = 0
                            title_w = max(1, max_label - fixed_w)
                        num_x = 1 + left_pad
                        title_x = num_x + num_w + col_gap
                        views_x = title_x + title_w + col_gap if views_w else None
                        likes_x = (
                            views_x + views_w + col_gap
                            if views_w and likes_w
                            else None
                        )

                        headers = [
                            (None, num_x, num_w, "#", True),
                            (
                                "title",
                                title_x,
                                title_w,
                                self._youtube_result_header_title(title_w),
                                False,
                            ),
                        ]
                        if views_w:
                            headers.append(("views", views_x, views_w, "Views", True))
                        if likes_w:
                            headers.append(("likes", likes_x, likes_w, "Likes", True))
                        for mode, x, width, title, align_right in headers:
                            if x is None or x >= scrollbar_col or width <= 0:
                                continue
                            if mode is not None and self.youtube_sort_mode == mode:
                                arrow = " ↓" if self.youtube_sort_reverse else " ↑"
                                title = f"{title}{arrow}"
                            title = self._truncate(title, width)
                            text = title.rjust(width) if align_right else title.ljust(width)
                            clip_w = max(0, min(width, scrollbar_col - x))
                            text = text[:clip_w]
                            self._safe(
                                self.win_lst,
                                1,
                                x,
                                text,
                                BOLD,
                            )
                            if mode is not None:
                                self._sort_col_regions.append(
                                    (x, min(x + width, scrollbar_col), mode)
                                )

                        if self._youtube_results_loading and not self.youtube_results:
                            msg = "Searching YouTube..."
                            self._safe(
                                self.win_lst,
                                2,
                                1,
                                self._truncate(msg, max_label).ljust(max_label),
                                DIM,
                            )
                        elif not self.search.strip():
                            msg = "Type a query in the search bar."
                            self._safe(
                                self.win_lst,
                                2,
                                1,
                                self._truncate(msg, max_label).ljust(max_label),
                                DIM,
                            )
                        elif not self.youtube_results:
                            msg = "No YouTube results."
                            self._safe(
                                self.win_lst,
                                2,
                                1,
                                self._truncate(msg, max_label).ljust(max_label),
                                DIM,
                            )

                        for i in range(vis):
                            idx = self.scroll + i
                            if idx >= total:
                                break
                            result = self.youtube_results[idx]
                            is_channel = self._is_youtube_channel_result(result)
                            raw_title = result.get("title", "Untitled")
                            if is_channel:
                                raw_title = f"[CHANNEL] {raw_title}"
                            display_title = self._truncate(
                                raw_title, title_w
                            ).ljust(title_w)
                            row_attr = BOLD if is_channel else N
                            cells = [
                                (num_x, num_w, str(idx + 1).rjust(num_w), row_attr),
                                (title_x, title_w, display_title, row_attr),
                            ]
                            if views_w:
                                views = (
                                    "channel"
                                    if is_channel
                                    else self._fmt_yt_count(result.get("views"))
                                ).rjust(views_w)
                                cells.append((views_x, views_w, views, row_attr))
                            if likes_w:
                                likes = (
                                    "open"
                                    if is_channel
                                    else self._fmt_yt_count(result.get("likes"))
                                ).rjust(likes_w)
                                cells.append((likes_x, likes_w, likes, row_attr))
                            row_y = i + 2
                            self._safe(self.win_lst, row_y, 1, " " * max_label, N)
                            for x, width, text, attr in cells:
                                if x is None or x >= scrollbar_col or width <= 0:
                                    continue
                                clip_w = max(0, min(width, scrollbar_col - x))
                                self._safe(
                                    self.win_lst,
                                    row_y,
                                    x,
                                    str(text)[:clip_w],
                                    attr,
                                )
                    else:
                        current = self.current
                        plays = self._plays_cache
                        left_pad = 2
                        select_w = 1
                        num_w = max(3, len(str(total)))
                        listen_w = 12
                        plays_w = max(7, cw)
                        duration_w = 10
                        last_w = 8
                        col_gap = 4
                        fixed_w = (
                            select_w
                            + num_w
                            + listen_w
                            + plays_w
                            + duration_w
                            + last_w
                        )
                        available_w = max(1, max_label - left_pad)
                        name_w = min(42, max(1, available_w - fixed_w - (col_gap * 6)))
                        select_x = 1 + left_pad
                        num_x = select_x + select_w + 1
                        name_x = num_x + num_w + col_gap
                        listen_x = name_x + name_w + col_gap
                        plays_x = listen_x + listen_w + col_gap
                        duration_x = plays_x + plays_w + col_gap
                        last_x = duration_x + duration_w + col_gap
                        self._row_click_end = min(
                            scrollbar_col, last_x + last_w
                        )
                        self._num_col_region = (
                            1,
                            min(num_x, scrollbar_col),
                        )

                        headers = [
                            (None, select_x, select_w, "○", False),
                            (None, num_x, num_w, "#", True),
                            ("name", name_x, name_w, "Name", False),
                            ("listen", listen_x, listen_w, "Play Time", True),
                            ("plays", plays_x, plays_w, "Plays", True),
                            ("duration", duration_x, duration_w, "Duration", True),
                            ("last", last_x, last_w, "Last", True),
                        ]
                        for mode, x, width, title, align_right in headers:
                            if x >= scrollbar_col:
                                continue
                            if mode is not None and self.sort_mode == mode:
                                arrow = " ↓" if self.sort_reverse else " ↑"
                                title = f"{title}{arrow}"
                            if (
                                mode is not None
                                and len(title) > width
                                and self.sort_mode == mode
                            ):
                                title = f"{title[: max(1, width - 2)]}{arrow}"
                            text = title.rjust(width) if align_right else title.ljust(width)
                            text = text[: max(0, min(width, scrollbar_col - x))]
                            attr = BOLD
                            self._safe(self.win_lst, 1, x, text, attr)
                            if mode is not None:
                                self._sort_col_regions.append(
                                    (x, min(x + width, scrollbar_col), mode)
                                )

                        for i in range(vis):
                            idx = self.scroll + i
                            if idx >= total:
                                break
                            name = self.songs[idx]
                            pc = plays.get(name, 0)
                            dur_ms = self._cached_duration_ms(name)
                            lt = self._fmt_listen_hours(
                                self._total_listen_hours(name)
                            )
                            duration = self._fmt_time(dur_ms) if dur_ms > 0 else "--:--"
                            last_played = self._fmt_since_last_play(name)
                            song_num = idx + 1
                            is_selected = name in self.selected_songs
                            select_mark = "●" if is_selected else "○"
                            display_name = self._truncate_song_name(name, name_w).ljust(name_w)
                            label = (
                                f"{' ' * left_pad}{select_mark}"
                                f" {str(song_num).rjust(num_w)}"
                                f"{' ' * col_gap}{display_name}"
                                f"{' ' * col_gap}{lt.rjust(listen_w)}"
                                f"{' ' * col_gap}{str(pc).rjust(plays_w)}"
                                f"{' ' * col_gap}{duration.rjust(duration_w)}"
                                f"{' ' * col_gap}{last_played.rjust(last_w)}"
                            )
                            is_current = name == current
                            if len(label) > max_label:
                                label = label[:max_label]
                            if is_current:
                                # Draw the label text with REV, then fill rest with normal spaces
                                self._safe(self.win_lst, i + 2, 1, label, REV | BOLD)
                                remaining = max_label - len(label)
                                if remaining > 0:
                                    self._safe(
                                        self.win_lst,
                                        i + 2,
                                        1 + len(label),
                                        " " * remaining,
                                        N,
                                    )
                            else:
                                padded = label.ljust(max_label)
                                self._safe(self.win_lst, i + 2, 1, padded, N)
                if total > vis and vis > 0:
                    thumb_size = max(1, int(vis * vis / total))
                    if thumb_size > vis:
                        thumb_size = vis
                    thumb_pos = (
                        int((vis - thumb_size) * self.scroll / mss) if mss > 0 else 0
                    )
                    for r in range(vis):
                        self._safe(
                            self.win_lst, r + 2, scrollbar_track_col, "│", DIM
                        )
                    for r in range(thumb_pos, thumb_pos + thumb_size):
                        if r >= vis:
                            break
                        self._safe(
                            self.win_lst, r + 2, scrollbar_track_col, "┃", BOLD
                        )
            except curses.error:
                pass
            self.win_lst.noutrefresh()

        # ── Input ──
        if self._needs_redraw_inp:
            self._needs_redraw_inp = False
            try:
                self.win_inp.erase()
                self._dialog_cancel_region = None
                self._rename_cancel_region = None
                if self.dialog_mode is not None:
                    self.win_inp.box()
                    curses.curs_set(1)
                    iw = self.win_inp.getmaxyx()[1]
                    label = " New playlist name: "
                    cancel_label = "[Cancel]"
                    cancel_x = max(1, iw - len(cancel_label) - 2)
                    if 2 + len(label) + 2 >= cancel_x:
                        label = " Name: "
                    self._safe(self.win_inp, 1, 2, label, BOLD)
                    x = 2 + len(label)
                    max_text_w = max(1, cancel_x - x - 1)
                    text = self.dialog_buf
                    cursor_draw_pos = self.dialog_cursor
                    if len(text) > max_text_w:
                        start = max(0, self.dialog_cursor - max_text_w + 1)
                        text = text[start : start + max_text_w]
                        cursor_draw_pos = self.dialog_cursor - start
                    self._safe(self.win_inp, 1, x, text.ljust(max_text_w), N)
                    self._safe(self.win_inp, 1, cancel_x, cancel_label, N)
                    self._dialog_cancel_region = (
                        cancel_x, cancel_x + len(cancel_label), 1
                    )
                    try:
                        cursor_x = x + min(cursor_draw_pos, max_text_w - 1)
                        self.win_inp.move(1, cursor_x)
                        self._text_cursor_target = (self.win_inp, 1, cursor_x)
                    except curses.error:
                        pass
                elif self.delete_confirm_songs and not self.confirm_open:
                    self._dialog_cancel_region = None
                    self.win_inp.attron(BOLD)
                    self.win_inp.box()
                    self.win_inp.attroff(BOLD)
                    curses.curs_set(0)
                    self._yt_preview_toggle_region = None
                    iw = self.win_inp.getmaxyx()[1]
                    count = len(self.delete_confirm_songs)
                    if count == 1:
                        target = self._truncate(
                            self.delete_confirm_songs[0], max(8, iw - 28)
                        )
                        label = f" Delete MP3 '{target}'? "
                    else:
                        label = f" Delete {count} MP3s? "
                    prompt = f"{label}Y/N"
                    self._safe(
                        self.win_inp,
                        1,
                        2,
                        self._truncate(prompt, max(1, iw - 4)).ljust(max(1, iw - 4)),
                        BOLD,
                    )
                elif self.mp3_rename_old or self.rename_active:
                    self.win_inp.attron(BOLD)
                    self.win_inp.box()
                    self.win_inp.attroff(BOLD)
                    curses.curs_set(1)
                    self._yt_preview_toggle_region = None
                    iw = self.win_inp.getmaxyx()[1]
                    name_y = 1
                    label = (
                        " Rename MP3: "
                        if self.mp3_rename_old
                        else " Rename playlist: "
                    )
                    label_x = 2
                    cancel_label = "[Cancel]"
                    cancel_x = max(1, iw - len(cancel_label) - 2)
                    if label_x + len(label) + 2 >= cancel_x:
                        label = " Name: "
                    self._safe(self.win_inp, name_y, label_x, label, BOLD)
                    field_x = label_x + len(label)
                    max_text_w = max(1, cancel_x - field_x - 1)
                    text = (
                        self.mp3_rename_buf
                        if self.mp3_rename_old
                        else self.rename_buf
                    )
                    cursor_draw_pos = (
                        self.mp3_rename_cursor
                        if self.mp3_rename_old
                        else self.rename_cursor
                    )
                    if len(text) > max_text_w:
                        start = max(0, cursor_draw_pos - max_text_w + 1)
                        text = text[start : start + max_text_w]
                        cursor_draw_pos -= start
                    self._safe(
                        self.win_inp,
                        name_y,
                        field_x,
                        text.ljust(max_text_w),
                        N,
                    )
                    self._safe(
                        self.win_inp, name_y, cancel_x, cancel_label, N
                    )
                    self._rename_cancel_region = (
                        cancel_x, cancel_x + len(cancel_label), name_y
                    )
                    cx = min(field_x + cursor_draw_pos, field_x + max_text_w - 1)
                    try:
                        self.win_inp.move(name_y, cx)
                        self._text_cursor_target = (self.win_inp, name_y, cx)
                    except curses.error:
                        pass
                elif self.download_rename_path:
                    self.win_inp.attron(BOLD)
                    self.win_inp.box()
                    self.win_inp.attroff(BOLD)
                    curses.curs_set(1)
                    self._yt_preview_toggle_region = None
                    iw = self.win_inp.getmaxyx()[1]
                    name_y = 1
                    label = " Name: "
                    label_x = 2
                    self._safe(self.win_inp, name_y, label_x, label, BOLD)
                    field_x = label_x + len(label)
                    max_text_w = max(1, iw - field_x - 2)
                    text = self.download_rename_buf
                    cursor_draw_pos = self.download_rename_cursor
                    if len(text) > max_text_w:
                        start = max(
                            0, self.download_rename_cursor - max_text_w + 1
                        )
                        text = text[start : start + max_text_w]
                        cursor_draw_pos = self.download_rename_cursor - start
                    self._safe(
                        self.win_inp,
                        name_y,
                        field_x,
                        text.ljust(max_text_w),
                        N,
                    )
                    cx = min(field_x + cursor_draw_pos, field_x + max_text_w - 1)
                    try:
                        self.win_inp.move(name_y, cx)
                        self._text_cursor_target = (self.win_inp, name_y, cx)
                    except curses.error:
                        pass
                else:
                    if self.focused == "search":
                        self.win_inp.attron(BOLD)
                        self.win_inp.box()
                        self.win_inp.attroff(BOLD)
                        curses.curs_set(1)
                    else:
                        self.win_inp.box()
                        if not self.rename_active:
                            curses.curs_set(0)
                    iw = self.win_inp.getmaxyx()[1]
                    search_y = 1
                    label = " Search: "
                    label_x = 2
                    toggle_label = "[YT Preview]"
                    toggle_w = len(toggle_label)
                    toggle_x = max(label_x + len(label) + 1, iw - toggle_w - 2)
                    self._yt_preview_toggle_region = (
                        toggle_x,
                        toggle_x + toggle_w,
                        search_y,
                        search_y + 1,
                    )
                    self._safe(
                        self.win_inp,
                        search_y,
                        label_x,
                        label,
                        BOLD if self.focused == "search" else N,
                    )
                    field_x = label_x + len(label)
                    max_text_w = max(1, toggle_x - field_x - 1)
                    text = self.search
                    cursor_draw_pos = self.search_cursor
                    if len(text) > max_text_w:
                        start = max(0, self.search_cursor - max_text_w + 1)
                        text = text[start : start + max_text_w]
                        cursor_draw_pos = self.search_cursor - start
                    self._safe(
                        self.win_inp,
                        search_y,
                        field_x,
                        text.ljust(max_text_w),
                        N,
                    )
                    toggle_attr = REV | BOLD if self.youtube_preview_enabled else N
                    self._safe(
                        self.win_inp,
                        search_y,
                        toggle_x,
                        toggle_label,
                        toggle_attr,
                    )
                    if self.focused == "search":
                        cx = min(field_x + cursor_draw_pos, field_x + max_text_w - 1)
                        try:
                            self.win_inp.move(search_y, cx)
                            self._text_cursor_target = (self.win_inp, search_y, cx)
                        except curses.error:
                            pass
            except curses.error:
                pass
            self.win_inp.noutrefresh()

        # ── Context menu ──
        if self._needs_redraw_ctx and self.ctx_menu_open:
            self._needs_redraw_ctx = False
            try:
                h, w = self.stdscr.getmaxyx()
                items = self.ctx_menu_items
                vis = self._ctx_menu_visible_count()
                content_w = self._ctx_menu_content_width()
                menu_w = content_w + 4
                my = self.ctx_menu_y
                mx = self.ctx_menu_x
                total_h = vis + 2
                if my + total_h >= h:
                    my = max(0, h - total_h)
                if mx + menu_w >= w:
                    mx = max(0, w - menu_w - 1)
                self._ctx_draw_y = my
                self._ctx_draw_x = mx
                self._ctx_draw_vis = vis
                self._ctx_draw_w = menu_w
                inner_w = menu_w - 2
                try:
                    self.stdscr.addstr(my, mx, "┌" + "─" * inner_w + "┐", N)
                except curses.error:
                    pass
                for i in range(vis):
                    idx = self.ctx_menu_scroll + i
                    if idx >= len(items):
                        break
                    action, _, label = items[idx]
                    row_y = my + 1 + i
                    if action == "separator":
                        try:
                            self.stdscr.addstr(row_y, mx, "├" + "─" * inner_w + "┤", N)
                        except curses.error:
                            pass
                    else:
                        padded = label[:inner_w].ljust(inner_w)
                        if i == self.ctx_menu_sel:
                            attr = REV | BOLD
                        elif action in ("remove", "delete_tab"):
                            attr = RED
                        else:
                            attr = N
                        try:
                            self.stdscr.addstr(row_y, mx, "│", N)
                            self.stdscr.addstr(row_y, mx + 1, padded, attr)
                            self.stdscr.addstr(row_y, mx + 1 + inner_w, "│", N)
                        except curses.error:
                            pass
                try:
                    self.stdscr.addstr(my + vis + 1, mx, "└" + "─" * inner_w + "┘", N)
                except curses.error:
                    pass
            except Exception:
                pass

        # ── Confirm dialog ──
        if self._needs_redraw_confirm and self.confirm_open:
            self._needs_redraw_confirm = False
            try:
                h, w = self.stdscr.getmaxyx()
                msg = self.confirm_msg
                btn_yes = " Delete "
                btn_no = " Cancel "
                inner_w = max(len(msg) + 4, len(btn_yes) + len(btn_no) + 16)
                if inner_w > w - 6:
                    inner_w = w - 6
                box_w = inner_w + 2
                box_h = 5
                dy = max(0, (h - box_h) // 2)
                dx = max(0, (w - box_w) // 2)
                self._confirm_y = dy
                self._confirm_x = dx
                self._confirm_w = box_w
                self._confirm_h = box_h
                try:
                    self.stdscr.addstr(dy, dx, "┌" + "─" * inner_w + "┐", BOLD)
                except curses.error:
                    pass
                msg_padded = msg[:inner_w].center(inner_w)
                try:
                    self.stdscr.addstr(dy + 1, dx, "│", BOLD)
                    self.stdscr.addstr(dy + 1, dx + 1, msg_padded, BOLD)
                    self.stdscr.addstr(dy + 1, dx + 1 + inner_w, "│", BOLD)
                except curses.error:
                    pass
                try:
                    self.stdscr.addstr(dy + 2, dx, "│" + " " * inner_w + "│", BOLD)
                except curses.error:
                    pass
                btn_row_y = dy + 3
                try:
                    self.stdscr.addstr(btn_row_y, dx, "│" + " " * inner_w + "│", BOLD)
                except curses.error:
                    pass
                total_btn_w = len(btn_yes) + 2 + 4 + len(btn_no) + 2
                btn_start = dx + 1 + max(0, (inner_w - total_btn_w) // 2)
                self._confirm_btn_regions = []

                yes_label = f"[{btn_yes}]"
                yes_attr = RED | BOLD
                try:
                    self.stdscr.addstr(btn_row_y, btn_start, yes_label, yes_attr)
                except curses.error:
                    pass
                self._confirm_btn_regions.append(
                    (btn_start, btn_start + len(yes_label), 0)
                )

                no_x = btn_start + len(yes_label) + 4
                no_label = f"[{btn_no}]"
                no_attr = N
                try:
                    self.stdscr.addstr(btn_row_y, no_x, no_label, no_attr)
                except curses.error:
                    pass
                self._confirm_btn_regions.append((no_x, no_x + len(no_label), 1))

                try:
                    self.stdscr.addstr(dy + 4, dx, "└" + "─" * inner_w + "┘", BOLD)
                except curses.error:
                    pass
            except Exception:
                pass

        # Refreshing another curses window can move the hardware cursor. Put
        # it back in the active text field as the final staged refresh.
        target = getattr(self, "_text_cursor_target", None) if self._active_text_field() else None
        if target is not None:
            try:
                cursor_win, cursor_y, cursor_x = target
                curses.curs_set(1)
                cursor_win.move(cursor_y, cursor_x)
                cursor_win.noutrefresh()
            except curses.error:
                pass
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

        try:
            curses.doupdate()
        except curses.error:
            pass

    def _shutdown_audio(self):
        if self._shutting_down:
            return
        self._save_session_if_due(force=True)
        self._shutting_down = True
        self.running = False
        self._duration_load_wakeup.set()
        self._output_safety_wakeup.set()
        # Apple Radio plays through the external Music app rather than
        # pygame, so it must be stopped explicitly on every shutdown path.
        self._stop_apple_radio_audio()
        self.apple_radio_enabled = False
        self.apple_radio_active = ""
        self.apple_radio_now = ""
        self.apple_radio_music_now = ""
        self._pending_media_toggle = False
        self._pending_shuffle_next = None
        self._clear_pending_play_incs()
        self._pending_youtube_result = None
        self._pending_youtube_error = None
        self._pending_preview_cleanup = False
        self.current = "None"
        self._song_len_ms = 0
        self._current_playback_path = ""
        self._play_start = 0.0
        self.paused = False
        if self._media_key_proc is not None:
            self._terminate_proc(self._media_key_proc)
            self._media_key_proc = None
        if self._find_my_log_proc is not None:
            self._terminate_proc(self._find_my_log_proc)
            self._find_my_log_proc = None
        if self._youtube_download_proc is not None:
            self._terminate_proc(self._youtube_download_proc)
            self._youtube_download_proc = None
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass
        try:
            pygame.mixer.quit()
        except Exception:
            pass

    def run(self):
        try:
            while self.running:
                self._poll_apple_radio_now()
                self._process_pending()
                self._enforce_playing_volume()
                self.draw()
                try:
                    ch = self.stdscr.getch()
                except Exception:
                    continue
                if ch == -1:
                    continue
                if ch == curses.KEY_RESIZE:
                    self._layout()
                    continue
                if ch == 27:
                    if self._try_read_sgr():
                        continue
                    if self.confirm_open:
                        self._confirm_no()
                        continue
                    if self.ctx_menu_open:
                        self._close_ctx_menu()
                        continue
                    if self.rename_active:
                        self._cancel_rename()
                        continue
                    if self.dialog_mode is not None:
                        self._cancel_dialog()
                        continue
                    if self.delete_confirm_songs:
                        self._cancel_delete_confirm()
                        continue
                    if self.mp3_rename_old:
                        self._cancel_mp3_rename()
                        continue
                    if self.download_rename_path:
                        self._cancel_download_rename()
                        continue
                    if self.focused == "search":
                        self._unfocus_search()
                        continue
                    continue
                self.dirty = True
                if self.confirm_open:
                    if ch in (ord("y"), ord("Y")):
                        self._confirm_yes()
                    elif ch in (ord("n"), ord("N"), 10, 13, 127, 8):
                        self._confirm_no()
                    continue
                if self.ctx_menu_open:
                    if ch in (10, 13):
                        self._ctx_menu_execute()
                    elif ch == curses.KEY_UP:
                        self._ctx_scroll_up()
                    elif ch == curses.KEY_DOWN:
                        self._ctx_scroll_down()
                    else:
                        self._close_ctx_menu()
                    continue
                if self.delete_confirm_songs:
                    if ch in (ord("y"), ord("Y")):
                        self._confirm_delete_mp3()
                    elif ch in (ord("n"), ord("N"), 127, 8):
                        self._cancel_delete_confirm()
                    elif ch in (10, 13):
                        self._cancel_delete_confirm()
                    continue
                if self.mp3_rename_old:
                    if ch in (10, 13):
                        self._finish_mp3_rename()
                    else:
                        self._edit_text_field("mp3_rename", ch)
                    continue
                if self.download_rename_path:
                    if ch in (10, 13):
                        self._finish_download_rename()
                    else:
                        self._edit_text_field("download_rename", ch)
                    continue
                if self.rename_active:
                    if ch in (10, 13):
                        self._finish_rename()
                    else:
                        self._edit_text_field("tab_rename", ch)
                    continue
                if self.dialog_mode is not None:
                    if ch in (10, 13):
                        self._finish_dialog()
                    else:
                        self._edit_text_field("dialog", ch)
                    continue
                if ch in (ord("e"), ord("E")) and self.focused != "search":
                    self._export_mp3_names()
                    continue
                if ch in (ord("p"), ord("P")) and self.focused != "search":
                    self.toggle_pause()
                    continue
                if ch in (ord("l"), ord("L")) and self.focused != "search":
                    self._toggle_song_switch_lock()
                    continue
                if ch in (ord("q"), ord("Q")) and self.focused != "search":
                    self._shutdown_audio()
                    break
                if self.focused == "search":
                    if ch in (10, 13):
                        if self.youtube_preview_enabled:
                            if self.youtube_results:
                                result = self.youtube_results[0]
                                if self._is_youtube_channel_result(result):
                                    self._open_youtube_channel(result)
                                else:
                                    self._start_youtube_preview(result)
                            else:
                                self._start_youtube_result_search()
                            self.focused = "list"
                            self._needs_redraw_inp = True
                            self._needs_redraw_lst = True
                            self._update_timeout()
                        elif self._start_youtube_preview():
                            self.focused = "list"
                            self._needs_redraw_inp = True
                            self._update_timeout()
                        else:
                            self.focused = "list"
                            self._apply_search_filter()
                            self._needs_redraw_inp = True
                            self._update_timeout()
                    elif ch == 9:
                        self.focused = "list"
                        self._needs_redraw_inp = True
                        self._needs_redraw_lst = True
                        self._update_timeout()
                    else:
                        self._edit_text_field("search", ch)
                    continue
        finally:
            self._shutdown_audio()
            self._stop_apple_radio_audio()
            self._youtube_request_id += 1
            self._youtube_search_request_id += 1
            self._youtube_download_request_id += 1
            self._terminate_proc(self._youtube_preview_proc)
            self._terminate_proc(self._youtube_search_proc)
            self._cleanup_youtube_preview(stop_audio=True)

if TEXTUAL_AVAILABLE:
    class PlaylistButton(Button):
        """A one-line playlist tab with a right-click action."""

        class ContextRequested(Message):
            def __init__(self, button, screen_x, screen_y):
                self.button = button
                self.screen_x = screen_x
                self.screen_y = screen_y
                super().__init__()

        async def _on_mouse_down(self, event):
            if event.button == 3:
                self.post_message(self.ContextRequested(
                    self, event.screen_x, event.screen_y,
                ))
                event.stop()
                return

        async def _on_click(self, event):
            if event.button == 3:
                event.stop()
                return


    class SongTable(DataTable):
        """Mouse-driven song list matching the original curses interaction."""

        can_focus = False
        DRAG_HOLD_SECONDS = 1.0

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._drag_candidate = None
            self._drag_origin = None
            self._drag_position = None
            self._dragging_songs = False
            self._suppress_click = False
            self._drag_press_id = 0
            self._drag_hold_timer = None
            self._music_highlighted_row_key = None

        def _render_cell(
            self, row_index, column_index, base_style, width,
            cursor=False, hover=False,
        ):
            """Render the active media row without moving the table cursor."""
            highlighted = self._music_highlighted_row_key
            if row_index >= 0 and highlighted is not None:
                try:
                    row_key = str(self._row_locations.get_key(row_index).value)
                except Exception:
                    row_key = ""
                cursor = row_key == highlighted
            else:
                cursor = False
            return super()._render_cell(
                row_index, column_index, base_style, width,
                cursor=cursor, hover=hover,
            )

        class SongClicked(Message):
            def __init__(
                self, row_key, column_index=0, button=1, shift=False,
                screen_x=0, screen_y=0,
            ):
                self.row_key = row_key
                self.column_index = column_index
                self.button = button
                self.shift = shift
                self.screen_x = screen_x
                self.screen_y = screen_y
                super().__init__()

        def _row_key_at_pointer(self, event):
            meta = event.style.meta
            row_index = meta.get("row")
            if row_index is None or row_index < 0:
                return None
            if not (0 <= row_index < len(self.ordered_rows)):
                return None
            return self.ordered_rows[row_index].key

        async def _on_mouse_down(self, event):
            player = getattr(self.app, "player", None)
            if (
                event.button != 1
                or player is None
                or player.youtube_preview_enabled
            ):
                return
            row_key = self._row_key_at_pointer(event)
            if row_key is None:
                return
            self._drag_candidate = str(row_key.value)
            self._drag_origin = (event.screen_x, event.screen_y)
            self._drag_position = self._drag_origin
            self._dragging_songs = False
            self._drag_press_id += 1
            press_id = self._drag_press_id
            self._drag_hold_timer = self.set_timer(
                self.DRAG_HOLD_SECONDS,
                lambda: self._begin_held_drag(press_id),
            )
            self.capture_mouse()

        def _begin_held_drag(self, press_id):
            if (
                press_id != self._drag_press_id
                or self._drag_candidate is None
                or self._drag_position is None
            ):
                return
            self._drag_hold_timer = None
            self._dragging_songs = True
            self.app.begin_song_drag(self._drag_candidate)
            self.app.update_song_drag(*self._drag_position)

        async def _on_mouse_move(self, event):
            if self._drag_candidate is None or self._drag_origin is None:
                return
            self._drag_position = (event.screen_x, event.screen_y)
            if self._dragging_songs:
                self.app.update_song_drag(event.screen_x, event.screen_y)
                event.stop()

        async def _on_mouse_up(self, event):
            if event.button != 1 or self._drag_candidate is None:
                return
            was_dragging = self._dragging_songs
            if was_dragging:
                self.app.finish_song_drag(event.screen_x, event.screen_y)
                self._suppress_click = True
                self.set_timer(0.2, self._clear_suppress_click)
                event.stop()
            if self._drag_hold_timer is not None:
                self._drag_hold_timer.stop()
                self._drag_hold_timer = None
            self.release_mouse()
            self._drag_press_id += 1
            self._drag_candidate = None
            self._drag_origin = None
            self._drag_position = None
            self._dragging_songs = False

        def _clear_suppress_click(self):
            self._suppress_click = False

        async def _on_click(self, event):
            if self._suppress_click:
                event.prevent_default()
                self._suppress_click = False
                event.stop()
                return
            meta = event.style.meta
            row_index = meta.get("row")
            column_index = meta.get("column")
            if row_index is None or column_index is None or row_index < 0:
                return
            if not (0 <= row_index < len(self.ordered_rows)):
                return
            # DataTable's default data-row action moves its black playback
            # cursor to the pointer row. Keep header clicks enabled so its
            # HeaderSelected message can still drive column sorting.
            event.prevent_default()
            self.post_message(self.SongClicked(
                self.ordered_rows[row_index].key,
                column_index,
                event.button,
                event.shift,
                event.screen_x,
                event.screen_y,
            ))
            event.stop()

        async def _on_key(self, event):
            # The curses list never had an MP3-row keyboard cursor.  In
            # particular, arrows must not silently move a hidden selection.
            if event.key in {
                "up", "down", "left", "right", "home", "end",
                "pageup", "pagedown", "enter",
            }:
                event.prevent_default()
                event.stop()
                return
            await super()._on_key(event)

        def on_resize(self, _event):
            # The table is resized one layout pass after the Screen when the
            # playlist sidebar appears or disappears. Refit from this final
            # width; fitting only from MusicApp.on_resize leaves stale column
            # widths behind and makes text progressively shorter.
            if getattr(self.app, "player", None):
                self.app.call_after_refresh(self.app._refit_resized_table)


    class DurationBar(Static):
        """A compact, one-cell-high playback position line."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.elapsed = 0.0
            self.duration = 0.0

        def set_progress(self, elapsed, duration):
            self.elapsed = max(0.0, float(elapsed or 0))
            self.duration = max(0.0, float(duration or 0))
            self.refresh()

        def render(self):
            width = max(1, self.size.width)
            ratio = min(1.0, self.elapsed / self.duration) if self.duration else 0.0
            filled = min(width, max(0, round(width * ratio)))
            bar = Text()
            bar.append("━" * filled, style="bold #111111")
            bar.append("─" * (width - filled), style="#c4c4c0")
            return bar


    class HorizontalRule(Static):
        """A responsive one-row divider."""

        def render(self):
            return Text("─" * max(1, self.size.width), style="#aaaaaa")


    class PlaylistResizeHandle(Static):
        """Mouse-drag handle between playlists and the song table."""

        can_focus = False

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.dragging = False
            self._drag_start_x = 0
            self._drag_start_width = 0

        def render(self):
            # Continue both panel borders across the divider; keep the
            # vertical spine strictly between them instead of poking out.
            height = max(1, self.size.height)
            if height == 1:
                glyphs = "─"
            else:
                glyphs = "\n".join(
                    "─" if row in (0, height - 1) else "│"
                    for row in range(height)
                )
            return Text(glyphs, style="#777777")

        async def _on_mouse_down(self, event):
            if event.button == 1:
                self.dragging = True
                self._drag_start_x = event.screen_x
                self._drag_start_width = self.app.query_one(
                    "#playlists", Vertical
                ).region.width
                self.capture_mouse()
                event.stop()

        async def _on_mouse_move(self, event):
            if self.dragging:
                pointer_delta = event.screen_x - self._drag_start_x
                self.app.resize_playlist_sidebar(
                    self._drag_start_width + pointer_delta
                )
                event.stop()

        async def _on_mouse_up(self, event):
            if self.dragging and event.button == 1:
                self.dragging = False
                self.release_mouse()
                pointer_delta = event.screen_x - self._drag_start_x
                self.app.resize_playlist_sidebar(
                    self._drag_start_width + pointer_delta, persist=True
                )
                event.stop()


    class LyricsResizeHandle(PlaylistResizeHandle):
        """Mouse-drag handle between the song table and lyrics sidebar."""

        async def _on_mouse_move(self, event):
            if self.dragging:
                self.app.resize_lyrics_sidebar(event.screen_x)
                event.stop()

        async def _on_mouse_up(self, event):
            if self.dragging and event.button == 1:
                self.dragging = False
                self.release_mouse()
                self.app.resize_lyrics_sidebar(event.screen_x, persist=True)
                event.stop()


    class ConfirmScreen(ModalScreen):
        """Small mouse-first confirmation dialog."""

        CSS = """
        ConfirmScreen { align: center middle; background: transparent; color: #111111; }
        #confirm-box { width: 56; max-width: 100%; height: auto; max-height: 100%; padding: 1 2; border: solid #333333; background: #fafaf7; }
        #confirm-message { width: 100%; height: auto; max-height: 1fr; overflow-y: auto; content-align: center middle; text-align: center; scrollbar-size: 1 1; }
        #confirm-actions { height: 3; align: center middle; }
        #confirm-actions Button { margin: 0 2; min-width: 12; }
        """

        def __init__(self, message):
            super().__init__()
            self.message = message

        def compose(self):
            with Vertical(id="confirm-box"):
                yield Label(self.message, id="confirm-message")
                with Horizontal(id="confirm-actions"):
                    yield Button("Cancel", id="confirm-cancel")
                    yield Button("✕ Delete", variant="error", id="confirm-yes")

        def on_mount(self):
            # Leave enough room for both action buttons, but size short prompts
            # to their text instead of always reserving the maximum width.
            maximum = max(1, int(self.size.width * 0.9))
            natural = cell_len(self.message) + 6  # border plus horizontal padding
            self.query_one("#confirm-box").styles.width = min(
                maximum, max(38, min(56, natural))
            )
            self._fit_to_screen()

        def on_resize(self, _event):
            self.call_after_refresh(self._fit_to_screen)

        def _fit_to_screen(self):
            """Keep the confirmation and its buttons inside tiny terminals."""
            box = self.query_one("#confirm-box")
            maximum = max(1, self.size.width)
            natural = cell_len(self.message) + 6
            box.styles.width = min(maximum, max(38, min(56, natural)))
            box.styles.max_height = max(1, self.size.height)
            for button in self.query_one("#confirm-actions").query(Button):
                button.styles.margin = (0, 0)
                button.styles.min_width = 1
                button.styles.width = "1fr"

        def on_button_pressed(self, event):
            self.dismiss(event.button.id == "confirm-yes")


    class HelpScreen(ModalScreen):
        """Compact reference for controls that are otherwise easy to miss."""

        BINDINGS = [("escape", "close_help", "Close")]
        CSS = """
        HelpScreen { align: center middle; background: transparent; color: #111111; }
        #help-box { width: 60; max-width: 92%; height: 26; max-height: 92%; padding: 0 2; border: solid #333333; background: #fafaf7; }
        #help-title { width: 100%; height: 1; text-style: bold; text-align: center; }
        #help-scroll { width: 100%; height: 1fr; margin-top: 1; overflow-y: auto; scrollbar-size: 1 1; }
        #help-text { width: 100%; height: auto; }
        #help-actions { height: 3; align: center middle; }
        #help-actions Button { min-width: 12; }
        """

        HELP_ITEMS = (
            ("Click / Enter", "Play an MP3 or preview a YouTube result"),
            ("Space", "Pause or resume"),
            ("Progress bar", "Click to seek"),
            ("Shift-click", "Select a range of MP3s"),
            ("Hold + drag", "Drag one or more MP3s to a playlist"),
            ("Right-click", "Rename, delete, or remove from playlist"),
            ("Apple Radio", "Open the upward station menu (macOS)"),
            ("Ctrl+N", "Create a playlist"),
            ("Ctrl+R", "Rename the selected MP3"),
            ("Delete", "Move the selected MP3 to Trash"),
            ("↻", "Rescan the library folder"),
            ("F1", "Open this help"),
        )
        HELP_DESCRIPTION_COLUMN = 18

        @classmethod
        def _wrapped_help_text(cls, width):
            width = max(cls.HELP_DESCRIPTION_COLUMN + 1, int(width))
            continuation = " " * cls.HELP_DESCRIPTION_COLUMN
            lines = []
            for shortcut, description in cls.HELP_ITEMS:
                prefix = f"{shortcut:<{cls.HELP_DESCRIPTION_COLUMN}}"
                lines.extend(
                    textwrap.wrap(
                        description,
                        width=width,
                        initial_indent=prefix,
                        subsequent_indent=continuation,
                        break_long_words=False,
                        break_on_hyphens=False,
                    )
                )
            return "\n".join(lines)

        def compose(self):
            with Vertical(id="help-box"):
                yield Static("HELP", id="help-title")
                with VerticalScroll(id="help-scroll"):
                    yield Static("", id="help-text")
                with Horizontal(id="help-actions"):
                    yield Button("Close", id="help-close")

        def on_mount(self):
            self.call_after_refresh(self._refresh_help_text)

        def on_resize(self, _event):
            self.call_after_refresh(self._refresh_help_text)

        def _refresh_help_text(self):
            help_text = self.query_one("#help-text", Static)
            # Reserve the scroll view's one-cell gutter so a scrollbar becoming
            # visible never clips the final description cell or moves wrapping.
            width = max(
                self.HELP_DESCRIPTION_COLUMN + 1,
                (help_text.content_size.width or help_text.size.width) - 1,
            )
            if getattr(self, "_help_wrap_width", None) == width:
                return
            self._help_wrap_width = width
            help_text.update(self._wrapped_help_text(width))
            # Auto-height layout can slightly change the final inner width.
            # Recheck after that layout pass and wrap once more if needed.
            self.call_after_refresh(self._refresh_help_text)

        def action_close_help(self):
            self.dismiss(None)

        def on_button_pressed(self, event):
            if event.button.id == "help-close":
                self.dismiss(None)


    class PlaylistMenuScreen(ModalScreen):
        """Rename/delete actions shown only after a playlist right-click."""

        BINDINGS = [("escape", "close_menu", "Close")]

        CSS = """
        PlaylistMenuScreen { align: left top; background: transparent; }
        #playlist-menu { width: 20; height: 5; padding: 0; border: solid #333333; background: #fafaf7; }
        #playlist-menu Button { width: 100%; min-width: 1; height: 1; padding: 0 1; border: none; text-align: left; content-align: left middle; }
        """

        def __init__(self, playlist_name, screen_x, screen_y):
            super().__init__()
            self.playlist_name = playlist_name
            self.menu_position = Offset(screen_x, screen_y)

        def compose(self):
            with Vertical(id="playlist-menu"):
                yield Button("✎ Rename", id="playlist-menu-rename")
                yield Button("# Edit tag", id="playlist-menu-tag")
                yield Button("✕ Delete", id="playlist-menu-delete")

        def on_mount(self):
            self._position_menu()

        def on_resize(self, _event):
            self.call_after_refresh(self._position_menu)

        def _position_menu(self):
            menu = self.query_one("#playlist-menu")
            menu_width = min(20, self.size.width)
            menu.styles.width = menu_width
            x = min(max(0, self.menu_position.x), max(0, self.size.width - menu_width))
            y = min(max(0, self.menu_position.y), max(0, self.size.height - 5))
            menu.styles.offset = Offset(x, y)

        def on_mouse_down(self, event):
            menu = self.query_one("#playlist-menu")
            region = menu.region
            if not (
                region.x <= event.screen_x < region.right
                and region.y <= event.screen_y < region.bottom
            ):
                event.stop()
                self.dismiss(None)

        def action_close_menu(self):
            self.dismiss(None)

        def on_button_pressed(self, event):
            action = {
                "playlist-menu-rename": "rename",
                "playlist-menu-tag": "tag",
                "playlist-menu-delete": "delete",
            }.get(event.button.id)
            self.dismiss(action)


    class SongMenuScreen(ModalScreen):
        """Rename/delete actions shown after right-clicking library songs."""

        BINDINGS = [("escape", "close_menu", "Close")]

        CSS = """
        SongMenuScreen { align: left top; background: transparent; }
        #song-menu { width: 24; height: 4; padding: 0; border: solid #333333; background: #fafaf7; }
        #song-menu Button { width: 100%; min-width: 1; height: 1; min-height: 1; max-height: 1; padding: 0 1; border: none; text-align: left; content-align: left middle; }
        """

        def __init__(self, songs, screen_x, screen_y, playlist_name=None):
            super().__init__()
            self.songs = list(songs)
            self.menu_position = Offset(screen_x, screen_y)
            self.playlist_name = playlist_name

        def compose(self):
            with Vertical(id="song-menu"):
                if len(self.songs) == 1:
                    yield Button("✎ Rename MP3", id="song-menu-rename")
                    yield Button("✕ Delete MP3", id="song-menu-delete")
                else:
                    yield Button(
                        f"✕ Delete {len(self.songs)} MP3s", id="song-menu-delete"
                    )
                if self.playlist_name:
                    yield Button("− Remove from playlist", id="song-menu-remove")

        def on_mount(self):
            self._position_menu()

        def on_resize(self, _event):
            self.call_after_refresh(self._position_menu)

        def _position_menu(self):
            action_count = (2 if len(self.songs) == 1 else 1) + bool(
                self.playlist_name
            )
            menu_height = action_count + 2
            menu = self.query_one("#song-menu")
            menu.styles.height = menu_height
            menu_width = min(24, self.app.size.width)
            menu.styles.width = menu_width
            x = min(max(0, self.menu_position.x), max(0, self.app.size.width - menu_width))
            y = min(
                max(0, self.menu_position.y),
                max(0, self.app.size.height - menu_height),
            )
            menu.styles.offset = Offset(x, y)

        def on_mouse_down(self, event):
            menu = self.query_one("#song-menu")
            region = menu.region
            if not (
                region.x <= event.screen_x < region.right
                and region.y <= event.screen_y < region.bottom
            ):
                event.stop()
                self.dismiss(None)

        def action_close_menu(self):
            self.dismiss(None)

        def on_button_pressed(self, event):
            action = {
                "song-menu-rename": "rename",
                "song-menu-delete": "delete",
                "song-menu-remove": "remove",
            }.get(event.button.id)
            self.dismiss(action)


    class AppleRadioMenuScreen(ModalScreen):
        """Station picker anchored immediately above the Apple Radio button."""

        BINDINGS = [("escape", "close_menu", "Close")]
        CSS = """
        AppleRadioMenuScreen { align: left top; background: transparent; }
        #apple-radio-menu { width: 28; height: 5; padding: 0; border: solid #333333; background: #fafaf7; }
        #apple-radio-menu Button { width: 100%; min-width: 1; height: 1; min-height: 1; max-height: 1; padding: 0 1; border: none; text-align: left; content-align: left middle; }
        #apple-radio-menu Button.active-station { background: #222222; color: #ffffff; text-style: none; }
        """

        def __init__(self, screen_x, screen_y, active_station=""):
            super().__init__()
            self.menu_position = Offset(screen_x, screen_y)
            self.active_station = active_station

        def compose(self):
            with Vertical(id="apple-radio-menu"):
                for index, station in enumerate(APPLE_RADIO_STATIONS):
                    name = str(station.get("name") or "Apple Music")
                    yield Button(
                        Text(name),
                        id=f"apple-radio-station-{index}",
                        classes="active-station" if name == self.active_station else "",
                    )

        def on_mount(self):
            self._position_menu()

        def on_resize(self, _event):
            self.call_after_refresh(self._position_menu)

        def _position_menu(self):
            menu = self.query_one("#apple-radio-menu")
            menu_width = min(28, self.size.width)
            menu_height = len(APPLE_RADIO_STATIONS) + 2
            menu.styles.width = menu_width
            menu.styles.height = menu_height
            x = min(max(0, self.menu_position.x), max(0, self.size.width - menu_width))
            # The passed y-coordinate is the top of the trigger button, so the
            # station list grows upward without covering the search controls.
            y = min(
                max(0, self.menu_position.y - menu_height),
                max(0, self.size.height - menu_height),
            )
            menu.styles.offset = Offset(x, y)

        def on_mouse_down(self, event):
            menu = self.query_one("#apple-radio-menu")
            region = menu.region
            if not (
                region.x <= event.screen_x < region.right
                and region.y <= event.screen_y < region.bottom
            ):
                event.stop()
                self.dismiss(None)

        def action_close_menu(self):
            self.dismiss(None)

        def on_button_pressed(self, event):
            button_id = event.button.id or ""
            if not button_id.startswith("apple-radio-station-"):
                return
            try:
                station_index = int(button_id.rsplit("-", 1)[-1])
            except ValueError:
                station_index = None
            self.dismiss(station_index)


    class MusicApp(App):
        """Responsive Textual front end for the Player engine."""

        TITLE = "MP3 Library"
        ENABLE_COMMAND_PALETTE = False
        # Textual 8+ otherwise lets mouse drags select arbitrary rendered text
        # (the blue highlight). Input maintains its own independent selection.
        ALLOW_SELECT = False
        CSS = """
        Screen { background: #fafaf7; color: #111111; layers: base drag-overlay; }
        Button { background: #fafaf7; color: #111111; border: solid #777777; text-style: none; }
        Button:hover { background: #fafaf7; color: #111111; border: solid #777777; background-tint: transparent; }
        Button:focus { background: #fafaf7; color: #111111; border: solid #111111; text-style: none; background-tint: transparent; }
        Button.-active { background: #fafaf7; color: #111111; border: solid #777777; tint: transparent; background-tint: transparent; }
        Input { background: #fafaf7; color: #111111; border: solid #777777; }
        Input:focus { background: #fafaf7; border: solid #111111; background-tint: transparent; }
        #top { height: 8; padding: 0 3; }
        #now-row { height: 2; align-vertical: middle; }
        #now { width: 1fr; content-align: left middle; text-style: bold; }
        #stats { width: auto; color: #777777; content-align: right middle; }
        #help-link { width: auto; min-width: 1; height: 1; padding: 0 1; border: none; text-style: underline; }
        #now-divider { height: 1; }
        #settings { width: 25; min-width: 25; height: 3; }
        #safety-row { height: 1; align-horizontal: right; }
        #non-speaker-label { width: 19; height: 1; content-align: right middle; }
        #non-speaker-toggle { width: auto; min-width: 1; height: 1; padding: 0 1; border: none; text-style: underline; }
        #tags-row { height: 1; align-horizontal: right; }
        #show-tags-label { width: 19; height: 1; content-align: right middle; }
        #show-tags-toggle { width: auto; min-width: 1; height: 1; padding: 0 1; border: none; text-style: underline; }
        #controls { height: 3; align-vertical: middle; }
        #playback-controls { width: 1fr; height: 3; align-vertical: middle; }
        #playback-controls Button { min-width: 5; width: auto; height: 3; margin-right: 1; }
        #volume-caption { width: 5; height: 3; content-align: left middle; text-style: bold; }
        #volume-label { width: 17; height: 3; padding-right: 1; content-align: center middle; }
        #transport-row { height: 1; margin-top: 1; align-vertical: top; }
        #time-label { width: auto; min-width: 16; max-width: 42; height: 1; padding-right: 1; color: #777777; content-align: left top; }
        #progress { width: 1fr; height: 1; color: #777777; }
        #workspace { height: 1fr; padding: 0 3; }
        #playlists { width: 23; min-width: 14; margin-right: 0; border: solid #777777; border-right: none; padding: 1; }
        #playlist-resizer { width: 1; min-width: 1; height: 1fr; }
        #playlist-header { width: 100%; height: 3; }
        #playlist-title { width: 1fr; height: 3; color: #777777; text-style: bold; content-align: left middle; }
        #library-refresh { width: 5; min-width: 5; max-width: 5; height: 3; padding: 0; border: solid #777777; text-style: none; content-align: center middle; }
        #playlist-buttons { height: 1fr; overflow-y: auto; }
        #playlist-buttons Button { width: 100%; height: 1; margin: 0; padding: 0 1; text-align: left; content-align: left middle; border: none; text-style: none; }
        #playlist-buttons Button.playback-source { text-style: bold; }
        #playlist-buttons Button.active-playlist { background: #222222; color: #ffffff; text-style: none; }
        #playlist-buttons Button.active-playlist.playback-source { background: #222222; color: #ffffff; text-style: bold; }
        #playlist-buttons Button.drop-target { background: #dbeafe; color: #111111; text-style: bold; }
        #playlist-buttons Button.new-playlist-link { color: #555555; text-style: underline; margin-top: 1; }
        #library { width: 1fr; border: solid #777777; border-left: none; }
        #lyrics-tab { width: 3; min-width: 3; max-width: 3; height: 8; margin-top: 1; padding: 0; border: none; background: #eeeeea; color: #222222; content-align: center middle; text-style: bold; }
        #lyrics-tab.open { background: #222222; color: #ffffff; }
        #lyrics-resizer { display: none; width: 1; min-width: 1; height: 1fr; }
        #lyrics-panel { display: none; width: 34; min-width: 8; border: solid #777777; border-left: none; background: #fafaf7; }
        #workspace.lyrics-open #library { border-right: none; }
        #workspace.lyrics-open #lyrics-resizer { display: block; }
        #workspace.lyrics-open #lyrics-panel { display: block; }
        #lyrics-header { height: 3; padding-left: 1; border-bottom: solid #777777; align-vertical: middle; }
        #lyrics-heading { width: 1fr; height: 3; content-align: left middle; text-style: bold; }
        #lyrics-close { width: 5; min-width: 5; max-width: 5; height: 3; padding: 0; border: none; content-align: center middle; }
        #lyrics-track { height: auto; max-height: 4; padding: 1 1 0 1; text-style: bold; }
        #lyrics-source { height: 2; padding: 0 1; color: #777777; }
        #lyrics-scroll { height: 1fr; padding: 0 1 1 1; scrollbar-size: 1 1; }
        #lyrics-content { width: 100%; height: auto; }
        #workspace.youtube #playlists { display: none; }
        #workspace.youtube #playlist-resizer { display: none; }
        #workspace.youtube #library { border-left: solid #777777; }
        #table { height: 1fr; scrollbar-size: 2 1; }
        DataTable { background: #fafaf7; color: #111111; }
        DataTable:focus { background-tint: transparent; }
        DataTable > .datatable--header { background: #fafaf7; color: #111111; text-style: bold; }
        DataTable > .datatable--header-hover { background: #fafaf7; color: #111111; }
        DataTable > .datatable--hover { background: #fafaf7; color: #111111; }
        DataTable > .datatable--cursor { background: #222222; color: #ffffff; text-style: bold; }
        DataTable > .datatable--odd-row { background: #fafaf7; }
        DataTable > .datatable--even-row { background: #fafaf7; }
        #editor { height: 4; margin: 0; padding: 0 1; border-top: solid #777777; align-vertical: middle; }
        #editor-label { width: 7; min-width: 7; height: 3; content-align: left middle; color: #555555; }
        #query { width: 1fr; }
        #yt-preview { width: auto; min-width: 1; margin-left: 1; }
        #apple-radio { width: auto; min-width: 1; margin-left: 1; }
        #cancel { width: auto; min-width: 1; margin-left: 1; }
        #drag-badge {
            display: none;
            position: absolute;
            layer: drag-overlay;
            width: auto;
            max-width: 42;
            height: 1;
            padding: 0 1;
            background: #222222;
            color: #ffffff;
            text-style: bold;
        }
        Screen.narrow #workspace { padding: 0; }
        Screen.narrow #playlists { width: 14; min-width: 14; }
        Screen.narrow #lyrics-panel { width: 29; }
        Screen.narrow #top { padding: 0 1; }
        Screen.tiny #playlists { display: none; }
        Screen.tiny #playlist-resizer { display: none; }
        Screen.tiny #library { border-left: solid #777777; }
        Screen.short #top { height: 6; }
        Screen.short #now-divider { display: none; }
        Screen.short #settings { display: none; }
        Screen.very-short #top { height: 5; }
        Screen.very-short #transport-row { display: none; }
        Screen.very-short #editor { display: none; }
        """

        BINDINGS = [
            ("space", "pause", "Play / Pause"),
            ("ctrl+n", "new_playlist", "New playlist"),
            ("ctrl+r", "rename_song", "Rename song"),
            ("delete", "delete_song", "Trash song"),
            ("f1", "show_help", "Help"),
            ("ctrl+q", "quit", "Quit"),
        ]

        def __init__(self, folder=DEFAULT_MP3_FOLDER):
            super().__init__()
            self.theme = "textual-light"
            self.folder = folder
            self.player = None
            self.editor_mode = "search"
            self.editor_context = None
            self.playlist_generation = 0
            self.playlist_ids = {}
            self._last_row_activation = (None, 0.0)
            self.last_clicked_song = None
            self._drag_songs = []
            self._drag_target_playlist = None
            self._drag_badge_width = 8
            self._lyrics_token = None
            self._lyrics_requested_token = None
            self._lyrics_enabled = False
            self._lyrics_width = None
            self._playlist_width = None

        def compose(self) -> ComposeResult:
            with Vertical(id="top"):
                with Horizontal(id="now-row"):
                    yield Static("NOW PLAYING  ▸  None", id="now")
                    yield Static("0 tracks", id="stats")
                    yield Button("Help", id="help-link")
                yield HorizontalRule(id="now-divider")
                with Horizontal(id="controls"):
                    with Horizontal(id="playback-controls"):
                        yield Static("VOL", id="volume-caption")
                        yield Button("−", id="vol-down")
                        yield Static("████······  40%", id="volume-label")
                        yield Button("+", id="vol-up")
                        yield Button("Pause", id="play-pause")
                        yield Button("Mode: loop", id="mode")
                        yield Button("Back", id="youtube-back", classes="youtube-action")
                        yield Button("Stop preview", id="youtube-stop", classes="youtube-action")
                    with Vertical(id="settings"):
                        with Horizontal(id="safety-row"):
                            yield Static("Non-Speaker Mode", id="non-speaker-label")
                            yield Button("Yes", id="non-speaker-toggle")
                        with Horizontal(id="tags-row"):
                            yield Static("Show Tags", id="show-tags-label")
                            yield Button("Yes", id="show-tags-toggle")
                with Horizontal(id="transport-row"):
                    yield Static("0:00 / 0:00", id="time-label")
                    yield DurationBar(id="progress")
            with Horizontal(id="workspace"):
                with Vertical(id="playlists"):
                    with Horizontal(id="playlist-header"):
                        yield Static("PLAYLISTS", id="playlist-title")
                        yield Button("↻", id="library-refresh")
                    yield Vertical(id="playlist-buttons")
                yield PlaylistResizeHandle(id="playlist-resizer")
                with Vertical(id="library"):
                    yield SongTable(
                        id="table", cursor_type="row", zebra_stripes=False,
                        show_cursor=False, cell_padding=1,
                    )
                    with Horizontal(id="editor"):
                        yield Static("Search", id="editor-label")
                        yield Input(placeholder="Filter your library…", id="query")
                        yield Button("Apple Radio", id="apple-radio")
                        yield Button("YT Preview", id="yt-preview")
                        yield Button("Cancel", id="cancel")
                yield Button("L\ny\nr\ni\nc\ns", id="lyrics-tab")
                yield LyricsResizeHandle(id="lyrics-resizer")
                with Vertical(id="lyrics-panel"):
                    with Horizontal(id="lyrics-header"):
                        yield Static("LYRICS", id="lyrics-heading")
                        yield Button("×", id="lyrics-close")
                    yield Static("", id="lyrics-track")
                    yield Static("", id="lyrics-source")
                    with VerticalScroll(id="lyrics-scroll"):
                        yield Static("Finding lyrics…", id="lyrics-content")
            yield Static("", id="drag-badge")

        def on_mount(self):
            self._main_screen = self.screen
            try:
                self.player = Player(folder=self.folder)
            except Exception as exc:
                self.notify(f"Audio initialization failed: {exc}", severity="error")
                self.exit(message=str(exc))
                return
            saved_widths = self.player.layout_widths
            self._playlist_width = saved_widths.get("playlists")
            self._lyrics_width = saved_widths.get("lyrics")
            if self._playlist_width is not None:
                self.query_one("#playlists", Vertical).styles.width = self._playlist_width
            if self._lyrics_width is not None:
                self.query_one("#lyrics-panel", Vertical).styles.width = self._lyrics_width
            self.call_after_refresh(self._clamp_playlist_sidebar_width)
            self.call_after_refresh(self._clamp_lyrics_sidebar_width)
            # Background completion events need their pending actions handled
            # immediately; waiting for the next periodic tick adds silence
            # between tracks. tick() still performs the lightweight refresh.
            self.player._ui_wakeup = lambda: self.call_from_thread(self.tick)
            self.player._ui_notify = self.notify
            table = self.query_one("#table", DataTable)
            self._configure_table_columns(False)
            self.refresh_playlists()
            self.refresh_ui(rebuild_table=True)
            self.set_interval(TEXTUAL_TICK_S, self.tick)
            # Native media-key and macOS safety integrations can perform
            # relatively expensive framework imports. Let the first screen
            # paint and become interactive before starting them.
            self.set_timer(1.0, self.player.start_background_services)

        def on_resize(self, event):
            # A resize event can arrive while Help or a context menu is the
            # current screen. Responsive classes belong to the player screen,
            # otherwise the layout remains stale after the modal closes.
            main_screen = getattr(self, "_main_screen", self.screen)
            main_screen.set_class(event.size.width < 82, "narrow")
            main_screen.set_class(event.size.width < 46, "tiny")
            main_screen.set_class(event.size.height < 16, "short")
            main_screen.set_class(event.size.height < 14, "very-short")
            if self.player:
                # Rebuild after the new widths settle so cells receive fresh
                # ellipses instead of Textual hard-clipping their old text.
                self.call_after_refresh(
                    lambda: self.refresh_ui(rebuild_table=True)
                )
            if self._playlist_width is not None:
                self.call_after_refresh(self._clamp_playlist_sidebar_width)
            self.call_after_refresh(self._clamp_lyrics_sidebar_width)

        def resize_playlist_sidebar(self, requested_width, persist=False):
            """Resize the playlist sidebar while preserving the other panes."""
            playlists = self.query_one("#playlists", Vertical)
            minimum, maximum = self._playlist_width_limits()
            width = max(minimum, min(maximum, round(requested_width)))
            self._playlist_width = width
            playlists.styles.width = width
            if persist and self.player:
                self.player.layout_widths["playlists"] = width
                self.player._save_cache()
            self.call_after_refresh(self._fit_table_columns)

        def _playlist_width_limits(self):
            """Keep the playlist pane from crowding the library or lyrics."""
            workspace = self.query_one("#workspace", Horizontal)
            lyrics_width = 0
            if workspace.has_class("lyrics-open"):
                lyrics_width = (
                    self.query_one("#lyrics-panel", Vertical).region.width + 1
                )
            # Account for the playlist divider and leave a useful song table.
            available = workspace.content_region.width - lyrics_width - 3 - 1 - 30
            minimum = min(14, max(8, available))
            maximum = max(minimum, min(42, available))
            return minimum, maximum

        def _workspace_width_for_terminal(self):
            width = self.screen.size.width
            return max(0, width - (0 if width < 82 else 6))

        def _clamp_playlist_sidebar_width(self):
            if self._playlist_width is None:
                return
            # Temporary terminal-size clamping must give the playlist its
            # preferred width before it allocates the remainder to lyrics.
            reserved_lyrics = 9 if self._lyrics_enabled else 0
            available = self._workspace_width_for_terminal() - 3 - 1 - 16 - reserved_lyrics
            minimum = min(14, max(8, available))
            maximum = max(minimum, min(42, available))
            width = max(minimum, min(maximum, self._playlist_width))
            self.query_one("#playlists", Vertical).styles.width = width
            self._fit_table_columns()

        def resize_lyrics_sidebar(self, screen_x, persist=False):
            """Resize the right sidebar from its left-hand drag handle."""
            panel = self.query_one("#lyrics-panel", Vertical)
            minimum, maximum = self._lyrics_width_limits()
            width = panel.region.right - screen_x
            width = max(minimum, min(maximum, width))
            self._lyrics_width = width
            panel.styles.width = width
            if persist and self.player:
                self.player.layout_widths["lyrics"] = width
                self.player._save_cache()
            self.call_after_refresh(self._refit_resized_table)

        def _lyrics_width_limits(self):
            """Keep both lyrics and library usable at the current terminal size."""
            left_width = self.query_one("#playlists", Vertical).region.width
            preferred_minimum = 18 if self.screen.size.width < 82 else 22
            # Leave at least 16 cells for the song table. On exceptionally
            # tiny terminals the sidebar may shrink as far as eight cells.
            available = max(
                8, self._workspace_width_for_terminal() - left_width - 3 - 1 - 16
            )
            minimum = min(preferred_minimum, available)
            maximum = max(minimum, min(64, available))
            return minimum, maximum

        def _clamp_lyrics_sidebar_width(self):
            minimum, maximum = self._lyrics_width_limits()
            preferred = self._lyrics_width
            if preferred is None:
                preferred = self.query_one("#lyrics-panel", Vertical).region.width or 34
            width = max(minimum, min(maximum, preferred))
            self.query_one("#lyrics-panel", Vertical).styles.width = width
            self._refit_resized_table()

        def begin_song_drag(self, song_name):
            if not self.player or song_name not in self.player.songs:
                self._drag_songs = []
                return
            if song_name in self.player.selected_songs:
                self._drag_songs = [
                    name
                    for name in self.player.songs
                    if name in self.player.selected_songs
                ]
            else:
                self._drag_songs = [song_name]
            self.query_one("#table", SongTable).add_class("dragging")
            badge = self.query_one("#drag-badge", Static)
            if len(self._drag_songs) == 1:
                label = f"{self._drag_songs[0]}.mp3"
                label = self.player._truncate_song_name(label, 38)
            else:
                label = f"{len(self._drag_songs)} MP3s"
            self._drag_badge_width = min(42, max(8, cell_len(label) + 2))
            # Textual parses plain strings as Rich markup. Artist prefixes such
            # as "[Aurora]" would therefore disappear unless wrapped in Text.
            badge.update(Text(label))
            badge.display = True

        def _playlist_drop_target(self, screen_x, screen_y):
            for button in self.query(PlaylistButton):
                name = self.playlist_ids.get(button.id or "")
                if (
                    name
                    and name != "All"
                    and button.region.contains(screen_x, screen_y)
                ):
                    return name, button
            return None, None

        def update_song_drag(self, screen_x, screen_y):
            if not self._drag_songs:
                return
            badge = self.query_one("#drag-badge", Static)
            x = min(max(0, screen_x + 1), max(0, self.screen.size.width - self._drag_badge_width))
            y = min(max(0, screen_y + 1), max(0, self.screen.size.height - 1))
            badge.styles.offset = Offset(x, y)
            target, target_button = self._playlist_drop_target(screen_x, screen_y)
            self._drag_target_playlist = target
            for button in self.query(PlaylistButton):
                button.set_class(button is target_button, "drop-target")

        def _clear_song_drag(self):
            self._drag_songs = []
            self._drag_target_playlist = None
            self.query_one("#table", SongTable).remove_class("dragging")
            self.query_one("#drag-badge", Static).display = False
            for button in self.query(PlaylistButton):
                button.remove_class("drop-target")

        def finish_song_drag(self, screen_x, screen_y):
            self.update_song_drag(screen_x, screen_y)
            target = self._drag_target_playlist
            songs = list(self._drag_songs)
            if target and songs:
                tagged = bool(self.player._playlist_tag(target))
                added = self.player._add_songs_to_playlist(target, songs)
                if not added and not tagged:
                    noun = "Song is" if len(songs) == 1 else "Songs are"
                    self.notify(
                        f"{noun} already in '{target}'", severity="warning"
                    )
            self._clear_song_drag()
            self.refresh_ui(rebuild_table=True)

        def _fit_table_columns(self):
            """Fill the table through its scrollbar with useful flex columns."""
            table = self.query_one("#table", DataTable)
            schema = getattr(table, "_music_schema", None)
            if schema == "youtube":
                flex_keys = ("title", "channel")
            else:
                flex_keys = ("name",)
            flex_columns = [table.columns.get(key) for key in flex_keys]
            if any(column is None for column in flex_columns):
                return
            fixed_columns = sum(
                column.width
                for column in table.columns.values()
                if column not in flex_columns
            )
            cell_padding = len(table.columns) * table.cell_padding * 2
            scrollbar_width = table.scrollbar_size_vertical
            flexible_width = max(
                0,
                table.size.width - fixed_columns - cell_padding - scrollbar_width - 1,
            )

            if schema == "youtube":
                # Both fields contain useful identifying information.  Keeping
                # Channel fixed at 22 meant a maximized window gave every new
                # cell to Title while channel names remained truncated.
                title_width = max(16, round(flexible_width * 0.6))
                channel_width = max(14, flexible_width - title_width)
                widths = (title_width, channel_width)
            else:
                widths = (max(8, flexible_width),)

            changed = False
            for column, width in zip(flex_columns, widths):
                if column.width == width:
                    continue
                column.width = width
                changed = True
            if changed:
                # Column.width is a plain value in Textual's DataTable; changing
                # it doesn't invalidate virtual_size by itself. Without this,
                # the horizontal scrollbar can retain the previous schema's
                # width until some unrelated table update occurs.
                table._require_update_dimensions = True
                table.refresh(layout=True)

        def _refit_resized_table(self):
            """Apply the table's settled width and refresh its ellipsized cells."""
            table = self.query_one("#table", DataTable)
            schema = getattr(table, "_music_schema", None)
            settled = (schema, table.size.width)
            if getattr(table, "_music_settled_size", None) == settled:
                return
            table._music_settled_size = settled
            table.scroll_to(x=0, animate=False, force=True)
            self._fit_table_columns()
            self.refresh_ui(rebuild_table=True)

        def _configure_table_columns(self, youtube, radio=False):
            table = self.query_one("#table", DataTable)
            wanted = "radio" if radio else "youtube" if youtube else "library"
            if getattr(table, "_music_schema", None) == wanted:
                return
            table.clear(columns=True)
            if radio:
                table.add_column("#", key="number", width=3)
                table.add_column("Station", key="station", width=28)
                table.add_column("Description", key="description", width=42)
            elif youtube:
                table.add_column("#", key="number", width=3)
                table.add_column(self._youtube_sort_heading("Title", "title"), key="title", width=42)
                table.add_column(self._youtube_sort_heading("Channel", "channel"), key="channel", width=22)
                table.add_column(self._youtube_sort_heading("Views", "views"), key="views", width=10)
                table.add_column(self._youtube_sort_heading("Likes", "likes"), key="likes", width=10)
            else:
                table.add_column("○", key="select", width=1)
                table.add_column("#", key="number", width=3)
                table.add_column(self._sort_heading("Name", "name"), key="name", width=32)
                table.add_column(self._sort_heading("Play Time", "listen"), key="listen", width=12)
                table.add_column(self._sort_heading("Plays", "plays"), key="plays", width=7)
                table.add_column(self._sort_heading("Duration", "duration"), key="duration", width=10)
                table.add_column(self._sort_heading("Last", "last"), key="last", width=8)
            table._music_schema = wanted

        def _settle_table_after_schema_change(self):
            """Refit a newly switched table after the workspace has laid out."""
            table = self.query_one("#table", DataTable)
            table.scroll_to(x=0, animate=False, force=True)
            self._fit_table_columns()
            # Rebuild once more so explicitly ellipsized cells use the final
            # post-sidebar width, rather than the preceding mode's width.
            self.refresh_ui(rebuild_table=True)

        def _sort_heading(self, title, mode):
            if self.player and self.player.sort_mode == mode:
                return f"{title} {'↓' if self.player.sort_reverse else '↑'}"
            return title

        def _youtube_sort_heading(self, title, mode):
            if self.player and self.player.youtube_sort_mode == mode:
                return f"{title} {'↓' if self.player.youtube_sort_reverse else '↑'}"
            return title

        def _refresh_sort_headings(self):
            table = self.query_one("#table", DataTable)
            schema = getattr(table, "_music_schema", None)
            if schema == "youtube":
                for key, title in (
                    ("title", "Title"), ("channel", "Channel"),
                    ("views", "Views"), ("likes", "Likes"),
                ):
                    column = table.columns.get(key)
                    if column is not None:
                        column.label = Text(self._youtube_sort_heading(title, key))
                table.refresh()
                return
            if schema != "library":
                return
            for key, title in (
                ("name", "Name"), ("listen", "Play Time"),
                ("plays", "Plays"), ("duration", "Duration"), ("last", "Last"),
            ):
                column = table.columns.get(key)
                if column is not None:
                    column.label = Text(self._sort_heading(title, key))
            table.refresh()

        def tick(self):
            if not self.player:
                return
            self.player._process_pending()
            self.player._queue_apple_radio_poll()
            self.player._save_session_if_due()
            self._sync_lyrics_panel()
            duration_updates = self.player._take_duration_ui_updates()
            if self.player.dirty:
                self.refresh_ui(rebuild_table=True)
            else:
                # Keep the clock isolated from the much more expensive table
                # and layout refresh. This callback runs once per second.
                self.refresh_transport()
                self._refresh_duration_cells(duration_updates)

        def _sync_lyrics_panel(self):
            """Update lyrics only while the user has opened the sidebar."""
            p = self.player
            if not p:
                return
            name = p.current if p.current in p._all_songs_set else ""
            token = (name, p._playback_id) if name else None
            workspace = self.query_one("#workspace", Horizontal)
            if token != self._lyrics_token:
                self._lyrics_token = token
            visible = self._lyrics_enabled
            visibility_changed = workspace.has_class("lyrics-open") != visible
            workspace.set_class(visible, "lyrics-open")
            self.query_one("#lyrics-tab", Button).set_class(visible, "open")
            if visibility_changed:
                self.call_after_refresh(self._clamp_lyrics_sidebar_width)
            if not visible:
                return
            if token != self._lyrics_requested_token:
                self._lyrics_requested_token = token
                p.request_lyrics(name)
            self.query_one("#lyrics-track", Static).update(Text(name or "No local song playing"))
            source = Text()
            if p.lyrics_source:
                source.append("Source: ")
                if p.lyrics_source_url:
                    source.append(
                        p.lyrics_source,
                        style=f"underline link {p.lyrics_source_url}",
                    )
                else:
                    source.append(p.lyrics_source)
            self.query_one("#lyrics-source", Static).update(source)
            content = (
                p.lyrics_text or p.lyrics_status
                or ("Finding lyrics…" if name else "Play a local MP3 to see lyrics.")
            )
            self.query_one("#lyrics-content", Static).update(Text(content))

        def toggle_lyrics_panel(self):
            self._lyrics_enabled = not self._lyrics_enabled
            if not self._lyrics_enabled:
                self._lyrics_requested_token = None
                self.player.request_lyrics("")
            self._sync_lyrics_panel()
            self.call_after_refresh(self._refit_resized_table)

        def _refresh_duration_cells(self, names):
            if not names:
                return
            p = self.player
            table = self.query_one("#table", DataTable)
            if getattr(table, "_music_schema", None) != "library":
                return
            existing_names = {
                str(row.key.value) for row in table.ordered_rows
            }
            for name in names:
                if name not in existing_names:
                    continue
                duration_ms = p._duration_ms_cache.get(name, 0)
                table.update_cell(
                    name,
                    "duration",
                    Text(
                        p._fmt_time(duration_ms) if duration_ms > 0 else "--:--",
                        justify="right",
                    ),
                )
                table.update_cell(
                    name,
                    "listen",
                    Text(
                        p._fmt_listen_hours(p._total_listen_hours(name)),
                        justify="right",
                    ),
                )

        def refresh_playlists(self):
            if not self.player:
                return
            box = self.query_one("#playlist-buttons", Vertical)
            box.remove_children()
            self.playlist_generation += 1
            self.playlist_ids = {}
            active = self.player._current_tab_name()
            source = self.player.play_tab
            for index, name in enumerate(self.player.tab_names):
                button_id = f"playlist-{self.playlist_generation}-{index}"
                self.playlist_ids[button_id] = name
                is_source = name == source
                marker = self._playlist_playback_marker(name)
                label = f"{marker} {name}" if marker else name
                button = PlaylistButton(Text(label), id=button_id)
                button.set_class(name == active, "active-playlist")
                button.set_class(is_source, "playback-source")
                box.mount(button)
            box.mount(
                Button(
                    "+ New playlist", id=f"new-playlist-{self.playlist_generation}",
                    classes="new-playlist-link",
                )
            )

        def _playlist_playback_marker(self, playlist_name):
            p = self.player
            if (
                not p
                or playlist_name != p.play_tab
                or p.current not in p._all_songs_set
            ):
                return ""
            # Match the narrow one-cell glyph used by the NOW PLAYING header.
            return "▸"

        def _sync_playlist_source_highlight(self):
            """Update source styling without rebuilding the whole sidebar."""
            if not self.player:
                return
            active = self.player._current_tab_name()
            source = self.player.play_tab
            for button_id, name in self.playlist_ids.items():
                try:
                    button = self.query_one(f"#{button_id}", PlaylistButton)
                except Exception:
                    continue
                is_source = name == source
                button.set_class(name == active, "active-playlist")
                button.set_class(is_source, "playback-source")
                marker = self._playlist_playback_marker(name)
                label = f"{marker} {name}" if marker else name
                if str(button.label) != label:
                    button.label = Text(label)

        @staticmethod
        def _fit_button_to_label(button, frame_cells=4):
            """Keep a button exactly wide enough for its current label."""
            target = cell_len(str(button.label)) + frame_cells
            if getattr(button, "_music_label_width", None) == target:
                return
            button._music_label_width = target
            button.styles.width = target
            button.styles.min_width = target
            button.styles.max_width = target
            button.refresh(layout=True)

        def _search_placeholder(self):
            return (
                "Search YouTube…"
                if self.player and self.player.youtube_preview_enabled
                else "Filter your library…"
            )

        def refresh_transport(self):
            """Refresh only elapsed time and progress, once per UI tick."""
            p = self.player
            if not p:
                return
            download_progress = p.youtube_download_progress or {}
            download_active = bool(
                p._youtube_download_loading and download_progress
            )
            elapsed, duration = p._current_pos_in_song()
            if download_active:
                phase = str(download_progress.get("phase") or "Downloading")
                percent = download_progress.get("percent")
                try:
                    percent = max(0.0, min(100.0, float(percent)))
                except (TypeError, ValueError):
                    percent = 0.0
                details = [f"{phase} {percent:.1f}%"]
                speed = str(download_progress.get("speed") or "").strip()
                eta = str(download_progress.get("eta") or "").strip()
                if speed:
                    details.append(speed)
                if eta:
                    details.append(f"ETA {eta}")
                self.query_one("#time-label", Static).update("  ".join(details))
                self.query_one("#progress", DurationBar).set_progress(percent, 100)
            elif p.apple_radio_enabled:
                self.query_one("#time-label", Static).update(
                    "Playing through Apple Music"
                    if p.apple_radio_active
                    else "Select a station below"
                )
                self.query_one("#progress", DurationBar).set_progress(0, 0)
            else:
                self.query_one("#time-label", Static).update(
                    f"{p._fmt_time(elapsed)} / {p._fmt_time(duration)}"
                )
                self.query_one("#progress", DurationBar).set_progress(
                    elapsed, duration
                )

        def refresh_ui(self, rebuild_table=False, preserve_scroll=True):
            p = self.player
            if not p:
                return
            self._sync_lyrics_panel()
            self._sync_playlist_source_highlight()
            if self.editor_mode == "search":
                self.query_one("#query", Input).placeholder = (
                    self._search_placeholder()
                )
            download_progress = p.youtube_download_progress or {}
            # Use a Text object so bracketed filename tags such as ``[OR]`` are
            # rendered literally instead of being consumed as Rich markup.
            if p._youtube_download_loading:
                phase = str(
                    download_progress.get("phase") or "Preparing download"
                ).strip().rstrip(".…")
                now_content = Text(f"{phase}…", style="bold")
            elif p._youtube_loading:
                now_content = Text("Preparing preview…", style="bold")
            elif p._youtube_results_loading or p._youtube_search_debounce_at:
                now_content = Text("Searching…", style="bold")
            elif p.apple_radio_enabled:
                radio_now = (
                    p._apple_radio_now_text()
                    if p.apple_radio_active
                    else "Choose a station"
                )
                now_content = Text.assemble(
                    ("APPLE MUSIC RADIO  ▸  ", "bold"), radio_now
                )
            else:
                now_content = Text.assemble(
                    ("NOW PLAYING  ▸  ", "bold"), p.current
                )
            self.query_one("#now", Static).update(now_content)
            self.query_one("#stats", Static).update(
                p._library_stats_label()
            )
            transport_visible = (
                not p.apple_radio_enabled
                and p.current not in ("None", "Loading...")
            )
            play_label = "Resume" if p.paused else "Pause"
            play_button = self.query_one("#play-pause", Button)
            play_button.display = transport_visible
            if str(play_button.label) != play_label:
                play_button.label = play_label
                play_button.refresh(layout=True)
            self._fit_button_to_label(play_button)
            level = min(10, max(0, round(p.vol * 10)))
            self.query_one("#volume-label", Static).update(
                f"{'█' * level}{'·' * (10 - level)}  {round(p.vol * 100):>3}%"
            )
            mode_button = self.query_one("#mode", Button)
            mode_label = "Download" if p.youtube_preview_enabled else f"Mode: {p.play_mode}"
            mode_button.display = True
            if str(mode_button.label) != mode_label:
                mode_button.label = mode_label
                mode_button.refresh(layout=True)
            self._fit_button_to_label(mode_button)
            safety_button = self.query_one("#non-speaker-toggle", Button)
            safety_button.label = "Yes" if p.non_speaker_mode else "No"
            self._fit_button_to_label(safety_button)
            tags_button = self.query_one("#show-tags-toggle", Button)
            tags_button.label = "Yes" if p.show_tags else "No"
            self._fit_button_to_label(tags_button)
            for selector in (
                "#vol-down", "#vol-up", "#youtube-back", "#youtube-stop",
                "#yt-preview", "#apple-radio", "#cancel", "#help-link",
            ):
                self._fit_button_to_label(self.query_one(selector, Button))
            workspace = self.query_one("#workspace", Horizontal)
            workspace.set_class(p.youtube_preview_enabled, "youtube")
            radio_button = self.query_one("#apple-radio", Button)
            radio_button.label = "Apple Radio"
            radio_button.display = (
                sys.platform == "darwin"
                and self.editor_mode == "search"
                and not p.youtube_preview_enabled
            )
            self._fit_button_to_label(radio_button)
            query = self.query_one("#query", Input)
            editor_label = self.query_one("#editor-label", Static)
            query.display = True
            editor_label.styles.width = 7
            if self.editor_mode == "search":
                editor_label.update("Search")
            self.query_one("#yt-preview", Button).display = (
                self.editor_mode == "search" and not p.youtube_preview_enabled
            )
            back = self.query_one("#youtube-back", Button)
            back.display = p.youtube_preview_enabled and p._youtube_view == "channel"
            stop = self.query_one("#youtube-stop", Button)
            stop.display = p.youtube_preview_enabled and (
                p._youtube_loading or p._is_youtube_preview_current()
            )
            self.refresh_transport()
            if rebuild_table:
                table = self.query_one("#table", DataTable)
                old_scroll_y = table.scroll_y if preserve_scroll else 0
                old_schema = getattr(table, "_music_schema", None)
                self._configure_table_columns(p.youtube_preview_enabled)
                schema_changed = old_schema != getattr(table, "_music_schema", None)
                if schema_changed:
                    # DataTable keeps its horizontal viewport when columns are
                    # replaced unless it is explicitly returned to the left.
                    table.scroll_to(x=0, animate=False, force=True)
                self._refresh_sort_headings()
                self._fit_table_columns()
                if not p.youtube_preview_enabled:
                    # The library columns are fitted to the viewport. Keep the
                    # viewport pinned left so trackpad side-scroll cannot hide
                    # leading artist tags such as [21P] or [Aurora].
                    table.scroll_to(x=0, animate=False, force=True)
                # SongTable paints the active media row with the cursor style,
                # but keeps Textual's actual navigation cursor parked at row 0.
                # Header sorting can then return to the top instead of chasing
                # whichever MP3 is currently playing.
                table.show_cursor = True
                table.move_cursor(row=0, column=0, scroll=False)
                if p.youtube_preview_enabled:
                    preview_active = (
                        p._youtube_loading or p._is_youtube_preview_current()
                    )
                    active_row_key = next(
                        (
                            f"youtube-{index}"
                            for index, result in enumerate(p.youtube_results)
                            if preview_active
                            and p._is_active_youtube_result(result)
                        ),
                        None,
                    )
                    if table._music_highlighted_row_key != active_row_key:
                        table._music_highlighted_row_key = active_row_key
                        table.refresh()
                    expected_keys = [f"youtube-{index}" for index in range(len(p.youtube_results))]
                    existing_keys = [str(row.key.value) for row in table.ordered_rows]
                    update_in_place = existing_keys == expected_keys
                    if not update_in_place:
                        table.clear()
                    for index, result in enumerate(p.youtube_results, 1):
                        is_channel = p._is_youtube_channel_result(result)
                        title = str(result.get("title") or "Untitled")
                        if is_channel:
                            title = f"[CHANNEL] {title}"
                        title = p._truncate(
                            title, table.columns.get("title").width
                        )
                        channel = p._truncate(
                            str(result.get("channel") or "--"),
                            table.columns.get("channel").width,
                        )
                        style = "bold" if is_channel else ""
                        values = (
                            Text(str(index), style=style, justify="left"),
                            Text(title, style=style),
                            Text(channel, style=style),
                            Text("channel" if is_channel else p._fmt_yt_count(result.get("views")), style=style, justify="right"),
                            Text("open" if is_channel else p._fmt_yt_count(result.get("likes")), style=style, justify="right"),
                        )
                        row_key = f"youtube-{index - 1}"
                        if update_in_place:
                            for column, value in zip(
                                ("number", "title", "channel", "views", "likes"),
                                values,
                            ):
                                table.update_cell(row_key, column, value)
                        else:
                            table.add_row(*values, key=row_key)
                    p.dirty = False
                    if not preserve_scroll:
                        table.scroll_to(y=0, animate=False, force=True)
                    elif not update_in_place:
                        self.call_after_refresh(
                            lambda: table.scroll_to(y=old_scroll_y, animate=False, force=True)
                        )
                    if schema_changed:
                        self.call_after_refresh(self._settle_table_after_schema_change)
                    return
                existing_names = [str(row.key.value) for row in table.ordered_rows]
                update_in_place = existing_names == list(p.songs)
                if not update_in_place:
                    table.clear()
                active_row_key = p.current if p.current in p.songs else None
                if table._music_highlighted_row_key != active_row_key:
                    table._music_highlighted_row_key = active_row_key
                    table.refresh()
                for index, name in enumerate(p.songs, 1):
                    duration_ms = p._cached_duration_ms(name)
                    def cell(value, justify="left"):
                        return Text(str(value), justify=justify)
                    values = (
                        cell("●" if name in p.selected_songs else "○"),
                        cell(index, "left"),
                        cell(p._truncate_song_name(
                            name if p.show_tags else p._without_artist_tag(name),
                            table.columns.get("name").width,
                        )),
                        cell(p._fmt_listen_hours(p._total_listen_hours(name)), "right"),
                        cell(p._plays(name), "right"),
                        cell(p._fmt_time(duration_ms) if duration_ms > 0 else "--:--", "right"),
                        cell(p._fmt_since_last_play(name), "right"),
                    )
                    if update_in_place:
                        for column, value in zip(
                            ("select", "number", "name", "listen", "plays", "duration", "last"),
                            values,
                        ):
                            table.update_cell(name, column, value)
                    else:
                        table.add_row(*values, key=name)
                self.call_after_refresh(
                    lambda: table.scroll_to(y=old_scroll_y, animate=False, force=True)
                )
                if schema_changed:
                    self.call_after_refresh(self._settle_table_after_schema_change)
                p.dirty = False

        def selected_song(self):
            if self.last_clicked_song in self.player.songs:
                return self.last_clicked_song
            if self.player.current in self.player.songs:
                return self.player.current
            return None

        def begin_editor(self, mode, value="", context=None):
            self.editor_mode = mode
            self.editor_context = context
            labels = {"search": "Search", "new_playlist": "New", "rename_playlist": "Rename", "playlist_tag": "Tag", "rename_song": "Rename MP3", "youtube_download_name": "Save as"}
            placeholders = {
                "search": self._search_placeholder(),
                "new_playlist": "Enter a new playlist name…",
                "rename_playlist": "Enter the playlist's new name…",
                "playlist_tag": "Artist tag only, without [square brackets]…",
                "rename_song": "Enter the MP3's new name…",
                "youtube_download_name": "Name this downloaded MP3…",
            }
            field = self.query_one("#query", Input)
            self.query_one("#editor-label", Static).update(labels.get(mode, "Edit"))
            self.query_one("#yt-preview", Button).display = (
                mode == "search"
                and bool(self.player)
                and not self.player.youtube_preview_enabled
            )
            field.placeholder = placeholders.get(mode, "Enter a value…")
            field.value = value
            field.cursor_position = len(value)
            field.focus()

        def finish_editor(self):
            value = self.query_one("#query", Input).value
            p = self.player
            if self.editor_mode == "new_playlist":
                p._create_playlist(value)
                self.refresh_playlists()
            elif self.editor_mode == "rename_playlist":
                p._rename_playlist(self.editor_context, value)
                self.refresh_playlists()
            elif self.editor_mode == "playlist_tag":
                if not p._set_playlist_tag(self.editor_context, value):
                    return
                self.refresh_playlists()
            elif self.editor_mode == "rename_song":
                p.mp3_rename_old = self.editor_context
                p.mp3_rename_buf = value
                p._finish_mp3_rename()
            elif self.editor_mode == "youtube_download_name":
                result = self.editor_context
                save_name = p._clean_mp3_basename(value)
                if not p._start_youtube_download(result, save_name=save_name):
                    self.refresh_ui()
                    return
            self.begin_editor("search", p.search)
            self.refresh_ui(rebuild_table=True)

        def begin_youtube_download(self):
            p = self.player
            result = p._youtube_download_result()
            if not isinstance(result, dict):
                p.status_msg = "No YouTube result to download"
                self.refresh_ui()
                return
            suggested_name = p._clean_mp3_basename(
                str(result.get("title") or "Untitled")
            )
            self.begin_editor(
                "youtube_download_name", suggested_name, dict(result)
            )

        def cancel_editor(self):
            p = self.player
            was_search = self.editor_mode == "search"
            self.editor_mode = "search"
            self.editor_context = None
            if was_search:
                p.search = ""
                p.search_cursor = 0
                p._apply_search_filter()
            field = self.query_one("#query", Input)
            field.value = p.search
            field.cursor_position = len(field.value)
            field.placeholder = self._search_placeholder()
            self.query_one("#editor-label", Static).update("Search")
            self.query_one("#yt-preview", Button).display = (
                not p.youtube_preview_enabled
            )
            # Cancel means leave the editor, not clear it and focus it again.
            self.set_focus(None)
            self.refresh_ui(rebuild_table=True)

        def cancel_current_mode(self):
            """Leave radio/YouTube and restore an unfiltered MP3 library."""
            p = self.player
            if p.apple_radio_enabled:
                p._exit_apple_radio_mode()
            elif p.youtube_preview_enabled or p._youtube_temp_path:
                p._exit_youtube_preview_mode()
            else:
                self.cancel_editor()
                return
            p.search = ""
            p.search_cursor = 0
            p._apply_search_filter()
            self.editor_mode = "search"
            self.editor_context = None
            field = self.query_one("#query", Input)
            field.value = ""
            field.cursor_position = 0
            field.placeholder = self._search_placeholder()
            self.query_one("#editor-label", Static).update("Search")
            self.set_focus(None)
            self.refresh_playlists()
            self.refresh_ui(rebuild_table=True, preserve_scroll=False)

        def on_input_changed(self, event):
            if event.input.id == "query" and self.editor_mode == "search" and self.player:
                self.player.search = event.value
                self.player.search_cursor = len(event.value)
                self.player._apply_search_filter()
                self.refresh_ui(rebuild_table=True, preserve_scroll=False)

        def on_input_submitted(self, event):
            if event.input.id == "query":
                if self.editor_mode == "search" and self.player.youtube_preview_enabled:
                    self.player._start_youtube_result_search()
                    self.refresh_ui(rebuild_table=True)
                    return
                self.finish_editor()

        def _play_row(self, row_key):
            name = str(row_key.value)
            now = time.monotonic()
            last_name, last_at = self._last_row_activation
            if name == last_name and now - last_at < 0.25:
                return
            self._last_row_activation = (name, now)
            if name in self.player.songs:
                self.player.play(name)
                # The original reverses the playing row immediately; don't
                # wait for the periodic refresh to catch up visually.
                self.refresh_ui(rebuild_table=True)

        def on_song_table_song_clicked(self, event):
            name = str(event.row_key.value)
            if event.button == 3:
                if self.player.youtube_preview_enabled or name not in self.player.songs:
                    return
                self.set_focus(None)
                self.last_clicked_song = name
                if name in self.player.selected_songs:
                    songs = [
                        song for song in self.player.songs
                        if song in self.player.selected_songs
                    ]
                else:
                    songs = [name]
                view_name = self.player._current_tab_name()
                manual_playlist = (
                    view_name
                    if view_name in self.player.playlists
                    and not self.player._playlist_tag(view_name)
                    else None
                )
                self.push_screen(
                    SongMenuScreen(
                        songs, event.screen_x, event.screen_y, manual_playlist
                    ),
                    lambda action: self._song_context_action(
                        songs, action, manual_playlist
                    ),
                )
                return
            if self.player.youtube_preview_enabled:
                self.set_focus(None)
                try:
                    index = int(name.removeprefix("youtube-"))
                    result = self.player.youtube_results[index]
                except (ValueError, IndexError):
                    return
                if self.player._is_youtube_channel_result(result):
                    self.player._open_youtube_channel(result)
                else:
                    self.player._start_youtube_preview(result)
                self.refresh_ui(rebuild_table=True)
                return
            self.last_clicked_song = name
            # This is the curses UI's list focus: it removes the text caret,
            # but deliberately does not create a keyboard-selected song row.
            self.set_focus(None)
            if event.column_index == 0:
                try:
                    index = self.player.songs.index(name)
                    self.player._select_song_number(
                        name, index, shift=event.shift
                    )
                finally:
                    self.refresh_ui(rebuild_table=True)
                return
            self._play_row(event.row_key)

        def _song_context_action(self, songs, action, playlist_name=None):
            songs = [song for song in songs if song in self.player.all_songs]
            if not songs or not action:
                return
            if action == "rename" and len(songs) == 1:
                self.begin_editor("rename_song", songs[0], songs[0])
            elif action == "remove" and playlist_name:
                self.player._remove_songs_from_playlist(playlist_name, songs)
                self.player._clear_selection_after_action()
                self.refresh_ui(rebuild_table=True)
            elif action == "delete":
                if len(songs) == 1:
                    message = f"Move '{songs[0]}.mp3' to Trash?"
                else:
                    message = f"Move {len(songs)} selected MP3s to Trash?"
                self.push_screen(
                    ConfirmScreen(message),
                    lambda yes: self._trash_songs(songs) if yes else None,
                )

        def _trash_songs(self, songs):
            self.player.delete_confirm_songs = list(songs)
            self.player._confirm_delete_mp3()
            self.player._clear_selection_after_action()
            self.refresh_ui(rebuild_table=True)

        def on_data_table_header_selected(self, event):
            """Sort immediately when a library or YouTube heading is clicked."""
            p = self.player
            if not p:
                return
            column_key = str(event.column_key.value)
            if p.youtube_preview_enabled:
                mode = {
                    "title": "title", "channel": "channel",
                    "views": "views", "likes": "likes",
                }.get(column_key)
                if mode is None:
                    return
                if p.youtube_sort_mode == mode:
                    p.youtube_sort_reverse = not p.youtube_sort_reverse
                else:
                    p.youtube_sort_mode = mode
                    p.youtube_sort_reverse = mode in {"views", "likes"}
                p._sort_youtube_results()
                self.refresh_ui(rebuild_table=True, preserve_scroll=False)
                return
            mode = {
                "name": "name", "listen": "listen", "plays": "plays",
                "duration": "duration", "last": "last",
            }.get(column_key)
            if mode is None:
                return
            if p.sort_mode == mode:
                p.sort_reverse = not p.sort_reverse
            else:
                p.sort_mode = mode
                p.sort_reverse = mode != "name"
            p._apply_search_filter()
            self.refresh_ui(rebuild_table=True, preserve_scroll=False)

        def on_button_pressed(self, event):
            button_id = event.button.id
            p = self.player
            if button_id in self.playlist_ids:
                name = self.playlist_ids[button_id]
                p.active_tab = p.tab_names.index(name)
                p.scroll = 0
                p._apply_search_filter()
                self.refresh_playlists()
                self.refresh_ui(rebuild_table=True, preserve_scroll=False)
            elif button_id == "vol-down": p._adjust_volume(-1)
            elif button_id == "vol-up": p._adjust_volume(1)
            elif button_id == "play-pause": p.toggle_pause()
            elif button_id == "mode":
                if p.youtube_preview_enabled: self.begin_youtube_download()
                else: p._switch_play_mode()
            elif button_id == "non-speaker-toggle":
                p.non_speaker_mode = not p.non_speaker_mode
                p._output_safety_wakeup.set()
                if not p.non_speaker_mode:
                    p._paused_by_speaker_safety = False
                if p.non_speaker_mode and p._is_builtin_speaker_name(p._current_output_device):
                    p._halt_for_speaker_safety()
            elif button_id == "show-tags-toggle":
                p.show_tags = not p.show_tags
                p._save_cache()
                self.refresh_ui(rebuild_table=True)
            elif button_id == "library-refresh":
                p._refresh_library()
                self.refresh_playlists()
                self.refresh_ui(rebuild_table=True)
            elif button_id in {"lyrics-close", "lyrics-tab"}:
                self.toggle_lyrics_panel()
            elif button_id == "help-link":
                self.action_show_help()
            elif button_id == "apple-radio":
                self.show_apple_radio_menu(event.button)
            elif button_id == "yt-preview": self.toggle_youtube_preview()
            elif button_id == "youtube-back": p._youtube_back_to_search()
            elif button_id == "youtube-stop": p._exit_current_youtube_preview()
            elif button_id.startswith("new-playlist-"): self.action_new_playlist()
            elif button_id == "cancel": self.cancel_current_mode()
            self.refresh_ui()

        def on_playlist_button_context_requested(self, event):
            button_id = event.button.id
            name = self.playlist_ids.get(button_id)
            if not name or name == "All":
                return
            self.push_screen(
                PlaylistMenuScreen(name, event.screen_x, event.screen_y),
                lambda action: self._playlist_context_action(name, action),
            )

        def _playlist_context_action(self, name, action):
            if action == "rename":
                self.begin_editor("rename_playlist", name, name)
            elif action == "tag":
                self.begin_editor(
                    "playlist_tag", self.player._playlist_tag(name), name
                )
            elif action == "delete":
                self.push_screen(
                    ConfirmScreen(f"Delete playlist '{name}'?"),
                    lambda yes: self._delete_playlist(name) if yes else None,
                )

        def toggle_youtube_preview(self):
            p = self.player
            if p.youtube_preview_enabled:
                p._exit_youtube_preview_mode()
                # A YouTube query should not unexpectedly become a library
                # filter when returning to the MP3 list.
                p.search = ""
                p.search_cursor = 0
                p._apply_search_filter()
            else:
                if p.apple_radio_enabled:
                    p._exit_apple_radio_mode()
                p.youtube_preview_enabled = True
                p.scroll = 0
                p.status_msg = "YouTube preview search enabled"
                p._apply_search_filter()
            self.editor_mode = "search"
            self.query_one("#editor-label", Static).update("Search")
            self.query_one("#query", Input).placeholder = self._search_placeholder()
            self.query_one("#query", Input).value = p.search
            self.refresh_playlists()
            self.refresh_ui(rebuild_table=True)

        def show_apple_radio_menu(self, button):
            p = self.player
            region = button.region
            self.push_screen(
                AppleRadioMenuScreen(
                    region.x, region.y, active_station=p.apple_radio_active
                ),
                self._select_apple_radio_station,
            )

        def _select_apple_radio_station(self, index):
            if index is None:
                return
            try:
                station = APPLE_RADIO_STATIONS[index]
            except (TypeError, IndexError):
                return
            p = self.player
            if p.youtube_preview_enabled:
                p._exit_youtube_preview_mode()
                p.search = ""
                p.search_cursor = 0
                p._apply_search_filter()
            p._play_apple_radio_station(station)
            self.editor_mode = "search"
            self.editor_context = None
            self.query_one("#query", Input).value = p.search
            self.refresh_playlists()
            self.refresh_ui(rebuild_table=True)

        def action_pause(self):
            if self.player: self.player.toggle_pause()

        def action_show_help(self):
            self.push_screen(HelpScreen())

        def action_new_playlist(self):
            self.begin_editor("new_playlist")

        def action_rename_playlist(self):
            name = self.player._current_tab_name()
            if name != "All": self.begin_editor("rename_playlist", name, name)

        def action_delete_playlist(self):
            name = self.player._current_tab_name()
            if name != "All":
                self.push_screen(ConfirmScreen(f"Delete playlist '{name}'?"), lambda yes: self._delete_playlist(name) if yes else None)

        def _delete_playlist(self, name):
            self.player._delete_playlist(name)
            self.refresh_playlists()
            self.refresh_ui(rebuild_table=True)

        def action_rename_song(self):
            name = self.selected_song()
            if name: self.begin_editor("rename_song", name, name)

        def action_delete_song(self):
            name = self.selected_song()
            if name:
                self.push_screen(ConfirmScreen(f"Move '{name}.mp3' to Trash?"), lambda yes: self._trash_song(name) if yes else None)

        def _trash_song(self, name):
            self.player.delete_confirm_songs = [name]
            self.player._confirm_delete_mp3()
            self.refresh_ui(rebuild_table=True)

        def on_unmount(self):
            if self.player:
                self.player._shutdown_audio()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mp3-player",
        description="Play and manage a folder of MP3 files in the terminal.",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=os.getcwd(),
        help="MP3 library folder (default: current directory)",
    )
    args = parser.parse_args(argv)
    folder = os.path.abspath(os.path.expanduser(args.folder))

    if not TEXTUAL_AVAILABLE:
        raise SystemExit("Textual is required. Install it with: python3 -m pip install textual")
    app = MusicApp(folder)
    try:
        app.run()
    finally:
        # Textual normally invokes on_unmount, but this also covers Ctrl+C,
        # startup failures, and exceptions during event handling.
        if app.player:
            app.player._shutdown_audio()


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
