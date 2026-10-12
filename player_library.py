"""Playlist, sorting, selection, and confirmation behavior."""

from player_common import *


class LibraryMixin:
    """Playlist, sorting, selection, and confirmation behavior."""

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
        current_artist = self._shuffle_artist_key(self.current)
        if current_artist:
            different_artist = [
                row for row in weighted
                if self._shuffle_artist_key(row["name"]) != current_artist
            ]
            # Avoid back-to-back tracks by the same tagged artist whenever
            # the active playlist has another artist to choose from. A
            # single-artist playlist keeps its normal weighted behavior.
            if different_artist:
                weighted = different_artist
        choices = [row["name"] for row in weighted]
        weights = [row["weight"] for row in weighted]
        if len(choices) == 1:
            return choices[0]
        return random.choices(choices, weights=weights, k=1)[0]

    @staticmethod
    def _shuffle_artist_key(name):
        """Return a generic artist identity when the filename supplies one."""
        label = str(name or "").strip()
        tagged = re.match(r"^\[([^\]]+)\]", label)
        if tagged:
            return tagged.group(1).strip().casefold()
        if " - " in label:
            artist, title = label.split(" - ", 1)
            if artist.strip() and title.strip():
                return artist.strip().casefold()
        return ""

    def _shuffle_weight_candidates(self):
        if self.songs:
            return list(self.songs)
        if self.play_pool:
            return list(self.play_pool)
        if self.play_tab == "All":
            return list(self.all_songs)
        return self._playlist_song_names(self.play_tab)

    def _live_shuffle_candidates(self):
        """Build the automatic-shuffle pool from MP3s present right now."""
        live_songs = self._scan_song_names()
        play_tab = getattr(self, "play_tab", "All")
        if play_tab == "All":
            return live_songs
        tag = self._playlist_tag(play_tab)
        if tag:
            prefix = f"[{tag}]".casefold()
            return [name for name in live_songs if name.casefold().startswith(prefix)]
        members = set(getattr(self, "playlists", {}).get(play_tab, []))
        return [name for name in live_songs if name in members]

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
