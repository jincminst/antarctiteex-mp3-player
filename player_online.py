"""Apple Music radio and YouTube search, preview, and download support."""

from player_common import *


class OnlineMixin:
    """Apple Music radio and YouTube search, preview, and download support."""

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
        self._sync_macos_media_state()
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
        self._sync_macos_media_state()
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
        if self._youtube_playlist_loading or self._youtube_playlist_total > 0:
            self._youtube_request_id += 1
            self._youtube_loading = False
            self._terminate_proc(self._youtube_preview_proc)
            self._cancel_youtube_playlist_preview()
            self._cleanup_youtube_preview(stop_audio=True)
            self.status_msg = "Canceled YouTube playlist preview"
            self._needs_redraw_hdr = True
            self._needs_redraw_bar = True
            self.dirty = True
            return True
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
        canceled_playlist = self._cancel_youtube_playlist_preview()
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
        self.status_msg = (
            "Canceled YouTube playlist preview"
            if canceled_playlist
            else "Deleted YouTube preview" if stopped else ""
        )
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
        playlist_id = self._youtube_playlist_id_from_query(query)
        if playlist_id:
            return f"playlist:{playlist_id}"
        video_id = self._youtube_video_id_from_query(query)
        if video_id:
            return f"video:{video_id}"
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

    def _is_youtube_playlist_result(self, result):
        return self._youtube_result_kind(result) == "playlist"

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

    def _youtube_playlist_target(self, result):
        if not isinstance(result, dict):
            return ""
        if not self._is_youtube_playlist_result(result):
            return ""
        url = self._youtube_abs_url(
            result.get("webpage_url") or result.get("url") or ""
        )
        if isinstance(url, str) and url.startswith("http"):
            return url
        playlist_id = str(result.get("id") or "").strip()
        if playlist_id:
            return (
                "https://www.youtube.com/playlist?"
                f"{urllib.parse.urlencode({'list': playlist_id})}"
            )
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

    @staticmethod
    def _youtube_video_id_from_query(query):
        """Return a bare YouTube video ID, without mistaking normal text for one."""
        value = str(query or "").strip()
        return value if re.fullmatch(r"[A-Za-z0-9_-]{11}", value) else ""

    @staticmethod
    def _youtube_playlist_id_from_query(query):
        """Return a playlist ID from a playlist URL or a recognizable bare ID."""
        value = str(query or "").strip()
        if not value:
            return ""
        try:
            parsed = urllib.parse.urlparse(
                value if "://" in value else f"https://{value}"
            )
            if parsed.netloc.lower().removeprefix("www.") in {
                "youtube.com",
                "m.youtube.com",
                "music.youtube.com",
                "youtu.be",
            }:
                playlist_ids = urllib.parse.parse_qs(parsed.query).get("list") or []
                if playlist_ids and re.fullmatch(
                    r"[A-Za-z0-9_-]{10,}", playlist_ids[0]
                ):
                    return playlist_ids[0]
        except (TypeError, ValueError):
            pass
        # Requiring a known prefix keeps normal searches from becoming IDs.
        if re.fullmatch(
            r"(?:PL|UU|LL|FL|UL|RD|OLAK5uy_)[A-Za-z0-9_-]{10,}", value
        ):
            return value
        return ""

    def _youtube_search_video_args(self, query, limit):
        video_id = self._youtube_video_id_from_query(query)
        if video_id:
            return (
                (
                    "--skip-download",
                    "--dump-single-json",
                    "--no-playlist",
                    f"https://www.youtube.com/watch?v={video_id}",
                ),
                True,
            )
        video_limit = max(1, limit)
        return (
            (
                "--flat-playlist",
                "--dump-single-json",
                "--playlist-end",
                str(video_limit),
                f"ytsearch{video_limit}:{query}",
            ),
            False,
        )

    def _youtube_search_playlist_args(self, query, limit):
        """Build a small playlist-only search without expanding its entries."""
        playlist_limit = max(1, int(limit))
        return (
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-end",
            str(playlist_limit),
            self._youtube_search_url(query, "EgIQAw=="),
        )

    def _youtube_direct_playlist_args(self, playlist_id):
        """Fetch one playlist's metadata without expanding the whole playlist."""
        target = (
            "https://www.youtube.com/playlist?"
            f"{urllib.parse.urlencode({'list': playlist_id})}"
        )
        return (
            "--flat-playlist",
            "--dump-single-json",
            "--playlist-end",
            "1",
            target,
        )

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
        required_results = (
            1
            if self._youtube_video_id_from_query(query)
            or self._youtube_playlist_id_from_query(query)
            else limit
        )
        if cache_key == self._youtube_results_query and (
            self._youtube_results_loading
            or len(self.youtube_results) >= required_results
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
        if cached and len(cached) >= required_results:
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
        if (
            self._is_youtube_channel_result(result)
            or self._is_youtube_playlist_result(result)
        ):
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
            if (
                not self._is_youtube_channel_result(result)
                and not self._is_youtube_playlist_result(result)
            ):
                return result
        for result in self.youtube_results:
            if self._is_youtube_playlist_result(result):
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
                (
                    0 if self._is_youtube_channel_result(item)
                    else 1 if self._is_youtube_playlist_result(item)
                    else 2
                ),
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

    def _extract_youtube_playlist_results(self, entries, limit):
        results = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("_type") or entry.get("ie_key") or "").lower()
            raw_url = self._youtube_abs_url(
                entry.get("webpage_url") or entry.get("url") or ""
            )
            playlist_id = str(
                entry.get("id") or entry.get("playlist_id") or ""
            ).strip()
            is_playlist = (
                "playlist" in kind
                or "list=" in raw_url
                or "/playlist" in raw_url
            )
            if not is_playlist:
                continue
            if not (isinstance(raw_url, str) and raw_url.startswith("http")):
                if not playlist_id:
                    continue
                raw_url = (
                    "https://www.youtube.com/playlist?"
                    f"{urllib.parse.urlencode({'list': playlist_id})}"
                )
            if not playlist_id:
                try:
                    playlist_id = (
                        urllib.parse.parse_qs(
                            urllib.parse.urlparse(raw_url).query
                        ).get("list") or [raw_url]
                    )[0]
                except Exception:
                    playlist_id = raw_url
            if playlist_id in seen:
                continue
            title = str(entry.get("title") or "Untitled playlist").strip()
            if not title:
                continue
            seen.add(playlist_id)
            results.append(
                {
                    "title": title,
                    "channel": str(
                        entry.get("channel")
                        or entry.get("uploader")
                        or entry.get("channel_title")
                        or ""
                    ).strip(),
                    "id": playlist_id,
                    "url": raw_url,
                    "views": None,
                    "likes": None,
                    "count": self._coerce_yt_int(
                        entry.get("playlist_count")
                        or entry.get("n_entries")
                        or entry.get("entry_count")
                    ),
                    "kind": "playlist",
                }
            )
            if len(results) >= limit:
                break
        return results

    def _extract_youtube_playlist_entries(self, data):
        """Return lightweight video rows in the playlist's original order."""
        entries = data.get("entries", []) if isinstance(data, dict) else []
        results = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "").strip()
            video_id = str(
                entry.get("id")
                or entry.get("display_id")
                or self._youtube_video_id_from_url(entry.get("url"))
                or ""
            ).strip()
            availability = str(entry.get("availability") or "").lower()
            if (
                not title
                or not video_id
                or video_id in seen
                or title.lower() in {"deleted video", "private video"}
                or availability in {"private", "premium_only", "subscriber_only"}
            ):
                continue
            seen.add(video_id)
            results.append(
                {
                    "title": title,
                    "channel": str(
                        entry.get("channel")
                        or entry.get("uploader")
                        or entry.get("channel_title")
                        or ""
                    ).strip(),
                    "id": video_id,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "views": entry.get("view_count"),
                    "likes": entry.get("like_count"),
                    "kind": "video",
                }
            )
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
            and not self._is_youtube_playlist_result(item)
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
                and not self._is_youtube_playlist_result(item)
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
        if YT_DLP_IMPERSONATE and self._yt_dlp_impersonation_available():
            cmd.extend(["--impersonate", YT_DLP_IMPERSONATE])
        if use_js_runtime:
            cmd.extend(self._yt_dlp_js_runtime_args())
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
    def _yt_dlp_js_runtime_args():
        """Enable the best installed EJS runtime using its absolute path."""
        for runtime, executable in YT_DLP_JS_RUNTIMES:
            path = shutil.which(executable)
            if path:
                return ["--js-runtimes", f"{runtime}:{path}"]
        return []

    def _yt_dlp_impersonation_available(self):
        """Return whether this yt-dlp install can use the requested browser."""
        cached = getattr(self, "_yt_dlp_impersonation_supported", None)
        if cached is not None:
            return cached
        lock = getattr(self, "_yt_dlp_impersonation_lock", None)
        if lock is None:
            lock = self._yt_dlp_impersonation_lock = threading.Lock()
        with lock:
            cached = getattr(self, "_yt_dlp_impersonation_supported", None)
            if cached is not None:
                return cached
            supported = False
            try:
                proc = subprocess.run(
                    [YT_DLP_BIN, "--ignore-config", "--list-impersonate-targets"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=4,
                    check=False,
                )
                target = YT_DLP_IMPERSONATE.casefold()
                for line in (proc.stdout or "").splitlines():
                    fields = line.strip().split()
                    if (
                        fields
                        and fields[0].casefold().startswith(target)
                        and "unavailable" not in line.casefold()
                    ):
                        supported = True
                        break
            except Exception:
                supported = False
            self._yt_dlp_impersonation_supported = supported
            return supported

    @staticmethod
    def _terminate_proc(proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass

    def _run_ytdlp_capture(self, cmd, proc_attr=None, timeout=None):
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc_attr:
            setattr(self, proc_attr, proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
            stderr = (stderr or "").rstrip()
            stderr += ("\n" if stderr else "") + "yt-dlp search timed out"
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
        lowered = err.lower()
        if "no supported javascript runtime" in lowered:
            return "yt-dlp needs Deno, Node, or QuickJS for YouTube"
        if (
            "signature solving failed" in lowered
            or "n challenge solving failed" in lowered
            or "external javascript" in lowered
            or "yt-dlp-ejs" in lowered
        ):
            return "yt-dlp-ejs is missing or outdated"
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

    def _run_ytdlp_capture_with_format_fallback(
        self, cmd, proc_attr=None, timeout=None
    ):
        proc = self._run_ytdlp_capture(cmd, proc_attr, timeout=timeout)
        if proc.returncode == 0 or not self._yt_dlp_requested_format_unavailable(proc):
            return proc
        return self._run_ytdlp_capture(
            self._without_ytdlp_format(cmd), proc_attr, timeout=timeout
        )

    def _run_ytdlp_with_cookie_retry(self, make_cmd):
        proc = self._run_ytdlp_with_format_fallback(make_cmd(False))
        if proc.returncode == 0 or not self._yt_dlp_needs_cookie_retry(proc):
            return proc
        return self._run_ytdlp_with_format_fallback(make_cmd(True))

    def _run_ytdlp_capture_with_cookie_retry(
        self, make_cmd, proc_attr=None, timeout=None
    ):
        proc = self._run_ytdlp_capture_with_format_fallback(
            make_cmd(False), proc_attr, timeout=timeout
        )
        if proc.returncode == 0 or not self._yt_dlp_needs_cookie_retry(proc):
            return proc
        return self._run_ytdlp_capture_with_format_fallback(
            make_cmd(True), proc_attr, timeout=timeout
        )

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
            timeout=YT_SEARCH_TIMEOUT_S,
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
        playlist_id = self._youtube_playlist_id_from_query(query)
        required_results = (
            1 if self._youtube_video_id_from_query(query) or playlist_id else limit
        )
        if cache_key == self._youtube_results_query and (
            self._youtube_results_loading
            or len(self.youtube_results) >= required_results
        ):
            return False
        cached = self._youtube_search_cache.get(cache_key)
        if cached and len(cached) >= required_results:
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
                if playlist_id:
                    playlist_data = self._run_youtube_search_json(
                        request_id,
                        *self._youtube_direct_playlist_args(playlist_id),
                    )
                    if playlist_data is None:
                        return
                    playlist_entry = dict(playlist_data or {})
                    playlist_entry["id"] = playlist_id
                    playlist_entry["playlist_id"] = playlist_id
                    playlist_entry["_type"] = "playlist"
                    playlist_entry["webpage_url"] = (
                        "https://www.youtube.com/playlist?"
                        f"{urllib.parse.urlencode({'list': playlist_id})}"
                    )
                    playlist_results = self._extract_youtube_playlist_results(
                        [playlist_entry], 1
                    )
                    self._pending_youtube_search_result = (
                        request_id,
                        cache_key,
                        playlist_results,
                        True,
                    )
                    return
                video_args, direct_video = self._youtube_search_video_args(
                    query, limit
                )
                data = self._run_youtube_search_json(request_id, *video_args)
                if data is None:
                    return
                entries = (
                    [data]
                    if direct_video and isinstance(data, dict)
                    else data.get("entries", []) if isinstance(data, dict) else []
                )
                video_results = [
                    item
                    for item in self._extract_youtube_search_results(entries, limit)
                    if not self._is_youtube_channel_result(item)
                ]
                if direct_video:
                    self._pending_youtube_search_result = (
                        request_id,
                        cache_key,
                        video_results[:limit],
                        True,
                    )
                    return

                channel_limit = min(YT_SEARCH_CHANNEL_LIMIT, max(2, limit // 8))
                channel_results = self._extract_youtube_channels_from_video_entries(
                    entries, channel_limit
                )
                initial_results = self._merge_youtube_results(
                    video_results, channel_results
                )
                self._pending_youtube_search_result = (
                    request_id,
                    cache_key,
                    initial_results,
                    False,
                )
                playlist_results = []
                try:
                    playlist_args = self._youtube_search_playlist_args(
                        query, YT_SEARCH_PLAYLIST_LIMIT
                    )
                    playlist_data = self._run_youtube_search_json(
                        request_id, *playlist_args
                    )
                    if playlist_data is None:
                        return
                    playlist_entries = (
                        playlist_data.get("entries", [])
                        if isinstance(playlist_data, dict)
                        else []
                    )
                    playlist_results = self._extract_youtube_playlist_results(
                        playlist_entries, YT_SEARCH_PLAYLIST_LIMIT
                    )
                except Exception:
                    # A filtered playlist search is supplementary. Keep the
                    # already loaded video/channel results if YouTube rejects it.
                    playlist_results = []
                results = self._merge_youtube_results(
                    initial_results, playlist_results
                )
                self._pending_youtube_search_result = (
                    request_id, cache_key, results, True
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
                    "--playlist-end",
                    str(self._youtube_result_limit()),
                    target,
                )
                if request_id != self._youtube_search_request_id:
                    return
                proc = self._run_ytdlp_capture_with_cookie_retry(
                    lambda use_cookies: self._yt_dlp_cmd(
                        *base_args, use_cookies=use_cookies
                    ),
                    "_youtube_search_proc",
                    timeout=YT_SEARCH_TIMEOUT_S,
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

    def _start_youtube_preview(self, result=None, playlist_item=False):
        if self._shutting_down:
            return False
        if self._is_youtube_playlist_result(result):
            return self._start_youtube_playlist_preview(result)
        if not playlist_item and (
            self._youtube_playlist_loading or self._youtube_playlist_total > 0
        ):
            self._cancel_youtube_playlist_preview()
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

    def _start_youtube_playlist_preview(self, result):
        target = self._youtube_playlist_target(result)
        if not target:
            self.status_msg = "Could not open that YouTube playlist"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False
        if self._youtube_playlist_loading or self._youtube_loading:
            return False
        if not self._yt_dlp_available():
            self.status_msg = "yt-dlp is required for playlist previews"
            self._needs_redraw_hdr = True
            self.dirty = True
            return False

        self._youtube_playlist_request_id += 1
        request_id = self._youtube_playlist_request_id
        title = str(result.get("title") or "YouTube playlist").strip()
        self._youtube_playlist_loading = True
        self._youtube_playlist_queue = deque()
        self._youtube_playlist_history = []
        self._youtube_playlist_total = 0
        self._youtube_playlist_completed = 0
        self._youtube_playlist_failed = 0
        self._youtube_playlist_title = title
        self._youtube_active_result = dict(result)
        self._pending_youtube_playlist_result = None
        self._pending_youtube_playlist_error = None
        self.status_msg = f"Loading playlist: {title}"
        self._needs_redraw_hdr = True
        self.dirty = True

        def worker():
            try:
                proc = self._run_ytdlp_capture_with_cookie_retry(
                    lambda use_cookies: self._yt_dlp_cmd(
                        "--flat-playlist",
                        "--dump-single-json",
                        "--ignore-errors",
                        target,
                        use_cookies=use_cookies,
                    ),
                    "_youtube_playlist_proc",
                )
                if request_id != self._youtube_playlist_request_id:
                    return
                if proc.returncode != 0:
                    raise RuntimeError(self._yt_dlp_error(proc))
                data = json.loads(proc.stdout or "{}")
                entries = self._extract_youtube_playlist_entries(data)
                if not entries:
                    raise RuntimeError("playlist has no available videos")
                self._pending_youtube_playlist_result = (
                    request_id, title, entries
                )
            except Exception as exc:
                self._pending_youtube_playlist_error = (request_id, str(exc))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _start_next_youtube_playlist_item(self):
        if self._youtube_playlist_loading or self._youtube_loading:
            return False
        if not self._youtube_playlist_queue:
            self._finish_youtube_playlist_preview()
            return False
        result = self._youtube_playlist_queue.popleft()
        self._youtube_playlist_history.append(result)
        position = len(self._youtube_playlist_history)
        title = str(result.get("title") or "Untitled").strip()
        self.status_msg = f"Preparing {position}/{self._youtube_playlist_total}: {title}"
        return self._start_youtube_preview(result, playlist_item=True)

    def _navigate_youtube_playlist(self, direction):
        """Move within the active temporary YouTube playlist preview."""
        if self._youtube_playlist_total <= 0:
            return False
        history = self._youtube_playlist_history
        if direction < 0:
            if len(history) < 2:
                self.status_msg = "Already at the first playlist video"
                self.dirty = True
                return False
            current = history.pop()
            previous = history.pop()
            self._youtube_playlist_queue.appendleft(current)
            self._youtube_playlist_queue.appendleft(previous)
        elif not self._youtube_playlist_queue:
            self.status_msg = "Already at the last playlist video"
            self.dirty = True
            return False

        # Invalidate an in-flight yt-dlp worker before deleting the current
        # temporary audio. The selected neighboring item then starts through
        # the same one-at-a-time queue used by natural playlist progression.
        self._youtube_request_id += 1
        self._youtube_loading = False
        self._terminate_proc(self._youtube_preview_proc)
        self._youtube_preview_proc = None
        self._pending_youtube_result = None
        self._pending_youtube_error = None
        self._pending_preview_cleanup = False
        self._cleanup_youtube_preview(stop_audio=True)
        return self._start_next_youtube_playlist_item()

    def _finish_youtube_playlist_preview(self):
        total = self._youtube_playlist_total
        completed = self._youtube_playlist_completed
        failed = self._youtube_playlist_failed
        if total <= 0:
            return
        self._youtube_playlist_queue = deque()
        self._youtube_playlist_history = []
        self._youtube_playlist_total = 0
        self._youtube_playlist_loading = False
        self._youtube_playlist_title = ""
        self._youtube_active_result = None
        if failed:
            self.status_msg = f"Playlist preview finished: {completed} played, {failed} skipped"
        else:
            self.status_msg = f"Playlist preview finished: {completed} songs"
        self._emit_notification(self.status_msg, timeout=7)
        self._needs_redraw_hdr = True
        self._needs_redraw_lst = True
        self.dirty = True

    def _cancel_youtube_playlist_preview(self):
        active = (
            self._youtube_playlist_loading
            or bool(self._youtube_playlist_queue)
            or self._youtube_playlist_total > 0
        )
        if not active:
            return False
        self._youtube_playlist_request_id += 1
        self._terminate_proc(self._youtube_playlist_proc)
        self._youtube_playlist_proc = None
        self._youtube_playlist_loading = False
        self._youtube_playlist_queue = deque()
        self._youtube_playlist_history = []
        self._youtube_playlist_total = 0
        self._youtube_playlist_title = ""
        self._pending_youtube_playlist_result = None
        self._pending_youtube_playlist_error = None
        return True

    def _start_youtube_download(self, result=None, save_name=None):
        if result is None:
            result = self._youtube_download_result()
        if self._is_youtube_playlist_result(result):
            return self._start_youtube_playlist_preview(result)
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
            if self._youtube_playlist_total > 0:
                position = len(self._youtube_playlist_history)
                self.status_msg = (
                    f"Playlist preview {position}/{self._youtube_playlist_total}; "
                    "temporary audio will be deleted when it ends"
                )
            else:
                self.status_msg = (
                    "YouTube preview will be deleted when it ends "
                    "or you play another track"
                )
            self._play_start = time.monotonic()
            self._handle_output_device(self._current_output_device)
            self._output_safety_wakeup.set()
        except Exception:
            self.status_msg = "Could not play YouTube preview"
            self._cleanup_youtube_preview(stop_audio=True)
        self._sync_macos_media_state()
        self._update_timeout()
