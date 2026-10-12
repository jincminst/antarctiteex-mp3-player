"""Duration analysis, volume normalization, and playback accounting."""

from player_common import *


class AudioMixin:
    """Duration analysis, volume normalization, and playback accounting."""

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
                if (
                    current in ("None", "Loading...")
                    or self.paused
                    or self._play_start <= 0
                ):
                    continue
                if self._is_youtube_preview_current():
                    if not pygame.mixer.music.get_busy():
                        self._pending_preview_cleanup = True
                        self.dirty = True
                        self._wake_ui()
                    continue
                if self.play_mode == "shuffle":
                    if not pygame.mixer.music.get_busy():
                        if self._pending_shuffle_next is None and self.play_pool:
                            self._queue_play_inc(current, playback_id, 1)
                            live_candidates = self._live_shuffle_candidates()
                            self._pending_shuffle_next = (
                                self._weighted_shuffle_choice(live_candidates)
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
