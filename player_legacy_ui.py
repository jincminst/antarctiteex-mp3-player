"""Legacy terminal layout, dialogs, selection, and text editing."""

from player_common import *


class LegacyUIMixin:
    """Legacy terminal layout, dialogs, selection, and text editing."""

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
        removed_songs = before - after
        removed = len(removed_songs)
        self._discard_cached_lyrics(removed_songs)
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
            # For a tagged playlist, only explicit manual members can be
            # removed; automatic filename matches remain supplied by its tag.
            pl_set = set(self.playlists.get(pl_name, []))
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
                self.lyrics_italics = []
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
        target = type(self)._unique_path(os.path.join(trash, os.path.basename(path)))
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
            self._discard_cached_lyrics([song])
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
        self._sync_macos_media_state()

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
