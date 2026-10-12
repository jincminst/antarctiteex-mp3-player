"""Playback controls, pending work, drawing, input, and shutdown."""

from player_common import *


class RuntimeMixin:
    """Playback controls, pending work, drawing, input, and shutdown."""

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
            self._handle_output_device(self._current_output_device)
            self._output_safety_wakeup.set()
            self._queue_volume_multiplier_compute(name)
        except Exception as exc:
            self.current = "None"
            self._song_len_ms = 0
            self._current_playback_path = ""
            self._set_playback_error(name, exc)
        self._sync_macos_media_state()
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
            self._handle_output_device(self._current_output_device)
            self._output_safety_wakeup.set()
            self._queue_volume_multiplier_compute(name)
        except Exception as exc:
            self.current = "None"
            self._song_len_ms = 0
            self._current_playback_path = ""
            self._set_playback_error(name, exc)
        self._sync_macos_media_state()
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
            if not was_paused:
                self._handle_output_device(self._current_output_device)
                self._output_safety_wakeup.set()
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
        self._sync_macos_media_state()
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
                self.non_speaker_mode
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
        self._sync_macos_media_state()
        self._update_timeout()

    def skip_track(self, direction):
        """Play the adjacent song in the current playback playlist."""
        if self.song_switch_locked:
            self.status_msg = "MP3 switch lock is on"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False
        pool = [name for name in self.play_pool if name in self._all_songs_set]
        if not pool:
            return False
        step = 1 if direction >= 0 else -1
        try:
            index = pool.index(self.current)
            target = pool[(index + step) % len(pool)]
        except ValueError:
            target = pool[0] if step > 0 else pool[-1]
        self.play(target)
        return self.current == target

    def _apply_volume(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_volume_apply_at < VOLUME_ENFORCE_S:
            return
        try:
            if not self._ensure_mixer_ready():
                return
            multiplier = max(0.0, min(1.0, float(self._current_volume_multiplier)))
            volume = max(0.0, min(1.0, self.vol * multiplier))
            if self.non_speaker_mode and self._is_builtin_speaker_name(
                self._current_output_device
            ):
                volume = 0.0
            pygame.mixer.music.set_volume(volume)
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
            or bool(getattr(self, "_pending_media_commands", ()))
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
            or self._pending_youtube_playlist_result is not None
            or self._pending_youtube_playlist_error is not None
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
            if self._youtube_playlist_total > 0:
                self._youtube_playlist_completed += 1
                self._start_next_youtube_playlist_item()
            else:
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
                playlist_item = self._youtube_playlist_total > 0
                if playlist_item:
                    self._youtube_playlist_failed += 1
                    self.status_msg = f"Skipped playlist preview: {error}"
                else:
                    self.status_msg = f"YouTube preview failed: {error}"
                self._emit_notification(
                    self.status_msg, severity="error", timeout=7
                )
                self._needs_redraw_hdr = True
                self.dirty = True
                if playlist_item:
                    self._start_next_youtube_playlist_item()
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
        if self._pending_youtube_playlist_error is not None:
            request_id, error = self._pending_youtube_playlist_error
            self._pending_youtube_playlist_error = None
            if request_id == self._youtube_playlist_request_id:
                self._youtube_playlist_loading = False
                self.status_msg = f"YouTube playlist failed: {error}"
                self._emit_notification(
                    self.status_msg, severity="error", timeout=7
                )
                self._needs_redraw_hdr = True
                self.dirty = True
        if self._pending_youtube_playlist_result is not None:
            request_id, title, entries = self._pending_youtube_playlist_result
            self._pending_youtube_playlist_result = None
            if request_id == self._youtube_playlist_request_id:
                self._youtube_playlist_loading = False
                self._youtube_playlist_queue = deque(entries)
                self._youtube_playlist_total = len(entries)
                self._youtube_playlist_completed = 0
                self._youtube_playlist_failed = 0
                self._youtube_playlist_title = title
                self.status_msg = f"Playlist ready: {len(entries)} songs"
                self._start_next_youtube_playlist_item()
                self._needs_redraw_hdr = True
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
        with self._media_button_lock:
            media_commands = list(getattr(self, "_pending_media_commands", ()))
            if hasattr(self, "_pending_media_commands"):
                self._pending_media_commands.clear()
        for command in media_commands:
            if command == "TOGGLE":
                self.toggle_pause()
            elif command == "PLAY" and self.paused:
                self.toggle_pause()
            elif command == "PAUSE" and not self.paused:
                self.toggle_pause()
            elif command == "NEXT":
                self.skip_track(1)
            elif command == "PREVIOUS":
                self.skip_track(-1)
            changed = True
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
            return RuntimeMixin._truncate(text, width)
        artist_tag, title = match.groups()
        tag_width = cell_len(artist_tag)
        if tag_width >= width:
            # The column cannot contain anything beyond the tag. Showing the
            # complete tag is more useful than turning it into "[Art...".
            return artist_tag.rstrip()
        return artist_tag + RuntimeMixin._truncate(title, width - tag_width)

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
                            is_playlist = self._is_youtube_playlist_result(result)
                            raw_title = result.get("title", "Untitled")
                            if is_channel:
                                raw_title = f"[CHANNEL] {raw_title}"
                            elif is_playlist:
                                raw_title = f"[PLAYLIST] {raw_title}"
                            display_title = self._truncate(
                                raw_title, title_w
                            ).ljust(title_w)
                            row_attr = BOLD if (is_channel or is_playlist) else N
                            cells = [
                                (num_x, num_w, str(idx + 1).rjust(num_w), row_attr),
                                (title_x, title_w, display_title, row_attr),
                            ]
                            if views_w:
                                views = (
                                    "channel"
                                    if is_channel
                                    else (
                                        f"{result.get('count')} tracks"
                                        if is_playlist and result.get("count")
                                        else "playlist" if is_playlist
                                        else self._fmt_yt_count(result.get("views"))
                                    )
                                ).rjust(views_w)
                                cells.append((views_x, views_w, views, row_attr))
                            if likes_w:
                                likes = (
                                    "open"
                                    if is_channel
                                    else "preview" if is_playlist
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
        self._remove_output_change_listener()
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
        self._pending_youtube_playlist_result = None
        self._pending_youtube_playlist_error = None
        self._pending_preview_cleanup = False
        self.current = "None"
        self._song_len_ms = 0
        self._current_playback_path = ""
        self._play_start = 0.0
        self.paused = False
        self._sync_macos_media_state()
        if self._media_key_proc is not None:
            self._terminate_proc(self._media_key_proc)
            self._media_key_proc = None
        if self._media_key_helper_path:
            try:
                os.unlink(self._media_key_helper_path)
            except OSError:
                pass
            self._media_key_helper_path = ""
        if self._find_my_log_proc is not None:
            self._terminate_proc(self._find_my_log_proc)
            self._find_my_log_proc = None
        if self._youtube_download_proc is not None:
            self._terminate_proc(self._youtube_download_proc)
            self._youtube_download_proc = None
        if self._youtube_playlist_proc is not None:
            self._terminate_proc(self._youtube_playlist_proc)
            self._youtube_playlist_proc = None
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
