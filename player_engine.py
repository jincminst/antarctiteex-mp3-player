"""Player engine assembled from the focused behavior mixins."""

from player_common import *
from player_library import LibraryMixin
from player_system import SystemMixin
from player_data import DataMixin
from player_audio import AudioMixin
from player_legacy_ui import LegacyUIMixin
from player_online import OnlineMixin
from player_runtime import RuntimeMixin


class Player(
    LibraryMixin,
    SystemMixin,
    DataMixin,
    AudioMixin,
    LegacyUIMixin,
    OnlineMixin,
    RuntimeMixin,
):
    """Music library and playback engine assembled from focused components."""

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
            self.play_tab = saved_playlist

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
        self._pending_media_commands = deque()
        self._last_media_button_at = 0.0
        self._media_button_lock = threading.Lock()
        self._last_volume_apply_at = 0.0
        self._pending_youtube_result = None
        self._pending_youtube_error = None
        self._pending_youtube_playlist_result = None
        self._pending_youtube_playlist_error = None
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
        self.lyrics_italics = []
        self.lyrics_bold = []
        self.lyrics_source = ""
        self.lyrics_source_url = ""
        self._lyrics_loading = False
        self._lyrics_request_id = 0
        self._lyrics_memory_cache = {}

        self._youtube_loading = False
        self._youtube_request_id = 0
        self._youtube_download_loading = False
        self._youtube_download_request_id = 0
        self._youtube_playlist_loading = False
        self._youtube_playlist_request_id = 0
        self._youtube_playlist_queue = deque()
        self._youtube_playlist_history = []
        self._youtube_playlist_total = 0
        self._youtube_playlist_completed = 0
        self._youtube_playlist_failed = 0
        self._youtube_playlist_title = ""
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
        self._youtube_playlist_proc = None

        self._screen_locked = False
        self._paused_by_lock = False
        self._paused_by_speaker_safety = False
        self._media_key_proc = None
        self._media_key_helper_path = ""
        self.non_speaker_mode = True
        self.show_tags = settings.get("show_tags", True) is not False
        self._saved_session = saved_session
        self._last_session_save_at = time.monotonic()
        self._last_session_snapshot = None
        self._current_output_device = ""
        self._output_safety_wakeup = threading.Event()
        self._output_route_changed = threading.Event()
        self._audio_output_listener = None
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
        self._rebuild_play_pool()
        if (
            saved_session.get("song") in self._all_songs_set
            and saved_session["song"] not in self.play_pool
        ):
            self.play_tab = "All"
            self._rebuild_play_pool()
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
            self._start_output_safety_watcher()
            self._start_media_key_listener()
            self._start_screen_lock_watcher()
            self._start_find_my_alert_watcher()

        threading.Thread(target=worker, daemon=True).start()
