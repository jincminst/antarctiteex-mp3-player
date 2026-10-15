"""Cache persistence, song metadata, and lyrics lookup."""

from player_common import *


class DataMixin:
    """Cache persistence, song metadata, and lyrics lookup."""

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

    def _merge_legacy_song_history(self, current, legacy):
        """Merge an old metadata file without rolling newer counters back."""
        merged = dict(current)
        for name, legacy_entry in legacy.items():
            if name not in merged:
                merged[name] = legacy_entry
                continue

            current_count = self._extract_count(merged[name])
            legacy_count = self._extract_count(legacy_entry)
            if legacy_count <= current_count:
                continue

            current_entry = merged[name]
            if isinstance(current_entry, dict):
                current_entry = dict(current_entry)
            else:
                current_entry = {"play_count": current_count}
            current_entry["play_count"] = legacy_count
            current_entry.pop("plays", None)
            current_entry.pop("count", None)
            if isinstance(legacy_entry, dict):
                current_last = self._last_played_value(current_entry)
                legacy_last = self._last_played_value(legacy_entry)
                if legacy_last > current_last:
                    current_entry["last_played_at"] = legacy_last
            merged[name] = current_entry
        return merged

    @staticmethod
    def _last_played_value(entry):
        if not isinstance(entry, dict):
            return 0
        values = []
        for key in ("last_played_at", "last_play_count_at"):
            try:
                values.append(max(0, int(entry.get(key, 0) or 0)))
            except (TypeError, ValueError, OverflowError):
                pass
        return max(values, default=0)

    def _load_cache(self):
        path = self._cache_path()
        cache = self._empty_cache()
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

            # Accept a bare legacy object at the new path. This also keeps old
            # callers that imported META_FILE or PLAYLISTS_FILE working.
            elif isinstance(data, dict):
                if data and all(isinstance(value, list) for value in data.values()):
                    cache["playlists"] = data
                else:
                    cache["songs"] = data

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
                if key == "songs":
                    cache[key] = self._merge_legacy_song_history(cache[key], value)
                else:
                    # The unified cache is newer. Keep its values while still
                    # rescuing keys that exist only in a leftover legacy file.
                    cache[key] = {**value, **cache[key]}

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
                    # The cache can contain full lyric documents. Compact JSON
                    # substantially reduces serialization work and disk I/O.
                    json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
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
            if (embedded_artist and embedded_artist.casefold() != artist.casefold()
                    and self._artist_matches_tag(embedded_artist, tag)):
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
    def _artist_number_words(number):
        """Spell a number in an artist name so 21P and TOP can agree."""
        ones = (
            "zero", "one", "two", "three", "four", "five", "six",
            "seven", "eight", "nine", "ten", "eleven", "twelve",
            "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
            "eighteen", "nineteen",
        )
        tens = (
            "", "", "twenty", "thirty", "forty", "fifty", "sixty",
            "seventy", "eighty", "ninety",
        )
        if number < 0 or number > 99:
            return ()
        if number < 20:
            return (ones[number],)
        return (tens[number // 10],) + ((ones[number % 10],) if number % 10 else ())

    @classmethod
    def _artist_matches_tag(cls, artist, tag):
        """Match an artist's initials, treating 21 and twenty one alike."""
        normalized = lambda value: re.sub(
            r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(value or "")).casefold()
        )
        wanted = normalized(tag)
        if not wanted:
            return False
        # Artist names often join words with capitals (OneRepublic). Split
        # those boundaries before lowercasing so 1R and OR both match.
        artist_words = re.sub(
            r"(?<=[a-z])(?=[A-Z])", " ",
            unicodedata.normalize("NFKD", str(artist or "")),
        )
        words = re.findall(r"[a-z0-9]+", artist_words.casefold())
        if wanted == normalized(artist):
            return True
        expanded = []
        for word in words:
            expanded.extend(cls._artist_number_words(int(word)) if word.isdigit()
                            and len(word) <= 2 else (word,))
        if wanted == "".join(word[0] for word in expanded):
            return True
        number_phrases = {
            cls._artist_number_words(number): str(number)
            for number in range(100)
        }
        contracted = []
        index = 0
        while index < len(expanded):
            pair = tuple(expanded[index:index + 2])
            single = (expanded[index],)
            if pair in number_phrases:
                contracted.append(number_phrases[pair])
                index += 2
            elif (single in number_phrases and index + 1 < len(expanded)
                  and (expanded[index + 1],) in number_phrases):
                contracted.append(expanded[index])
                index += 1
            elif single in number_phrases:
                contracted.append(number_phrases[single])
                index += 1
            else:
                contracted.append(expanded[index])
                index += 1
        return wanted == "".join(word[0] if not word.isdigit() else word
                                 for word in contracted)

    @classmethod
    def _artist_tag_query_variants(cls, tag):
        """Try digits, their English words, and the resulting initials."""
        tag = str(tag or "").strip()
        variants = [tag] if tag else []
        spelled = re.sub(
            r"\d{1,2}",
            lambda match: " " + " ".join(
                cls._artist_number_words(int(match.group()))
            ) + " ",
            tag,
        )
        spelled = " ".join(spelled.split())
        if spelled and spelled.casefold() != tag.casefold():
            variants.append(spelled)
            initials = "".join(
                word[0] for word in spelled.split()
            ).upper()
            if initials.casefold() != tag.casefold():
                variants.append(initials)
        return variants

    @staticmethod
    def _normalized_lyrics_identity(value):
        """Normalize song/artist text for provider result matching."""
        value = unicodedata.normalize("NFKD", str(value or "")).casefold()
        return " ".join(re.findall(r"[a-z0-9]+", value))

    @classmethod
    def _lyrics_artist_matches(cls, candidate, known):
        """Match exact artists and differently formatted collaboration credits."""
        candidate = str(candidate or "").strip()
        known = str(known or "").strip()
        if not candidate or not known:
            return False
        candidate_normalized = cls._normalized_lyrics_identity(candidate)
        known_normalized = cls._normalized_lyrics_identity(known)
        if candidate_normalized == known_normalized:
            return True

        candidate_parts = set(cls._lyrics_artist_query_parts(candidate))
        known_parts = set(cls._lyrics_artist_query_parts(known))
        return bool(candidate_parts & known_parts)

    @classmethod
    def _lyrics_artist_query_parts(cls, artist):
        """Return normalized individual artists from a collaboration credit."""
        separator = re.compile(
            r"\s*(?:&|,|/|\bx\b|\bfeat(?:uring)?\.?|\bft\.?)\s*",
            re.IGNORECASE,
        )
        parts = []
        for part in separator.split(str(artist or "")):
            part = part.strip()
            normalized = cls._normalized_lyrics_identity(part)
            if normalized and normalized not in {
                cls._normalized_lyrics_identity(item) for item in parts
            }:
                parts.append(part)
        return parts

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
    def _lyrics_candidate_duration_seconds(candidate):
        """Read a provider candidate's duration without assuming one schema."""
        if not isinstance(candidate, dict):
            return 0.0
        try:
            duration = float(
                candidate.get("duration")
                or candidate.get("duration_seconds")
                or 0
            )
            if duration <= 0 and candidate.get("duration_ms"):
                duration = float(candidate["duration_ms"]) / 1000
            return duration if duration > 0 else 0.0
        except (TypeError, ValueError, OverflowError):
            return 0.0

    @classmethod
    def _lyrics_duration_matches(cls, candidate, target_seconds):
        """Reject a known duration that clearly belongs to another recording."""
        candidate_seconds = cls._lyrics_candidate_duration_seconds(candidate)
        try:
            target_seconds = float(target_seconds or 0)
        except (TypeError, ValueError, OverflowError):
            target_seconds = 0.0
        if candidate_seconds <= 0 or target_seconds <= 0:
            return True
        tolerance = max(4.0, target_seconds * 0.03)
        return abs(candidate_seconds - target_seconds) <= tolerance

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
            "duration_ms": 0,
        }
        if metadata["artist_tag"]:
            metadata["artist_hint"] = self._playlist_artist_for_tag(
                metadata["artist_tag"]
            )
        path = self._song_path(name)
        if shutil.which("ffprobe") and os.path.exists(path):
            try:
                proc = subprocess.run(
                    [
                        "ffprobe", "-v", "error", "-show_entries",
                        "format=duration:format_tags",
                        "-of", "json", path,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=5,
                    check=False,
                )
                payload = json.loads(proc.stdout or "{}")
                audio_format = payload.get("format", {})
                try:
                    metadata["duration_ms"] = max(
                        0, round(float(audio_format.get("duration") or 0) * 1000)
                    )
                except (TypeError, ValueError, OverflowError):
                    metadata["duration_ms"] = 0
                tags = audio_format.get("tags", {})
                if isinstance(tags, dict):
                    lowered = {
                        str(key).casefold(): value for key, value in tags.items()
                    }
                    metadata["artist"] = str(
                        lowered.get("artist") or artist
                    ).strip()
                    metadata["title"] = str(
                        lowered.get("title") or title
                    ).strip()
                    metadata["album"] = str(
                        lowered.get("album") or ""
                    ).strip()
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
        entry = getattr(self, "meta", {}).get(name, {})
        identity = entry.get("recognized_identity") if isinstance(entry, dict) else None
        if isinstance(identity, dict):
            recognized_artist = str(identity.get("artist") or "").strip()
            recognized_title = str(identity.get("title") or "").strip()
            if recognized_artist and recognized_title:
                # An audio fingerprint is stronger evidence than a filename
                # prefix such as [LoL], which may describe a franchise or
                # playlist rather than an artist.
                metadata["artist"] = recognized_artist
                metadata["artist_hint"] = recognized_artist
                metadata["artist_tag"] = ""
                metadata["title"] = recognized_title
                metadata["identity_source"] = "Shazam"
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
        try:
            target_seconds = float(metadata.get("duration_ms") or 0) / 1000
        except (TypeError, ValueError, OverflowError):
            target_seconds = 0
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
        for artist in (preferred_artist, known_artist, artist_hint,
                       *self._artist_tag_query_variants(tag)):
            for query_artist in (artist, *self._lyrics_artist_query_parts(artist)):
                if query_artist and self._normalized_lyrics_identity(query_artist) not in {
                    self._normalized_lyrics_identity(item) for item in query_artists
                }:
                    query_artists.append(query_artist)

        candidates = {}
        search_failed = False
        title_variants = self._lyrics_title_variants(title)
        for query_title in title_variants:
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
                    artist_exact = self._lyrics_artist_matches(
                        artist, known_artist
                    )
                    artist_hint_match = bool(artist_hint) and (
                        not known_artist or known_artist.casefold() == tag.casefold()
                    ) and self._lyrics_artist_matches(artist, artist_hint)
                    artist_tag_match = bool(tag) and self._artist_matches_tag(artist, tag)
                    if tag and not artist_tag_match:
                        continue
                    if not page_url or not title_score or not (
                        artist_exact or artist_hint_match or artist_tag_match
                    ):
                        continue
                    if not self._lyrics_duration_matches(result, target_seconds):
                        continue
                    score = title_score + (
                        50 if artist_exact or artist_hint_match else 35
                    )
                    candidate_seconds = self._lyrics_candidate_duration_seconds(result)
                    if candidate_seconds and target_seconds:
                        score += max(0, 12 - abs(candidate_seconds - target_seconds))
                    previous = candidates.get(page_url)
                    if previous is None or score > previous[0]:
                        candidates[page_url] = (score, result)
                if candidates:
                    break
            if candidates or search_failed:
                break

        # Abbreviations can make Genius search miss an otherwise obvious
        # title. A title-only query is safe because results still have to pass
        # the exact-title and artist checks above.
        if not candidates and not search_failed and tag:
            for query_title in title_variants:
                try:
                    payload = self._search_genius(query_title)
                except Exception:
                    break
                for hit in self._genius_song_hits(payload):
                    result = hit.get("result", {}) if isinstance(hit, dict) else {}
                    artist = str(result.get("primary_artist", {}).get("name", ""))
                    candidate_title = str(result.get("title") or result.get("full_title") or "")
                    page_url = str(result.get("url") or "").strip()
                    title_score = self._genius_title_score(candidate_title, title)
                    if (page_url and title_score
                            and self._artist_matches_tag(artist, tag)
                            and self._lyrics_duration_matches(
                                result, target_seconds
                            )):
                        candidates[page_url] = (title_score + 35, result)
                if candidates:
                    break

        for page_url, (_score, _result) in sorted(
            candidates.items(), key=lambda item: item[1][0], reverse=True
        ):
            try:
                parser = _GeniusLyricsParser()
                parser.feed(self._fetch_text(page_url))
            except Exception:
                continue
            marked = self._clean_lyrics_text(parser.marked_text())
            lyrics, italics = _extract_lyric_italics(marked)
            if lyrics:
                return {"text": lyrics, "italics": italics,
                        "source": "Genius", "url": page_url}
        return None

    def _infer_unique_lyrics_artist(self, title):
        """Infer an artist only when exact-title Genius results all agree."""
        title = str(title or "").strip()
        if not title:
            return "", 0
        payload = self._search_genius(title)
        artists = {}
        for hit in self._genius_song_hits(payload):
            result = hit.get("result", {}) if isinstance(hit, dict) else {}
            if self._genius_title_score(result.get("title", ""), title) != 100:
                continue
            artist = str(
                result.get("primary_artist", {}).get("name") or ""
            ).strip()
            normalized = self._normalized_lyrics_identity(artist)
            if normalized:
                artists.setdefault(normalized, artist)
        if len(artists) == 1:
            return next(iter(artists.values())), 1
        return "", len(artists)

    def _fetch_directory_lyrics(self, metadata):
        """Try embedded lyrics, Genius, and LRCLIB without recognition."""
        # Genius is preferred for a verified artist-tag match, but it may
        # occasionally reject automated requests. Keep LRCLIB as a reliable
        # fallback instead of turning a Genius outage into "unavailable".
        has_artist = any(
            str(metadata.get(key) or "").strip()
            for key in ("artist", "artist_hint", "artist_tag")
        )
        if not has_artist and metadata.get("lyrics"):
            return {
                "text": metadata["lyrics"],
                "source": "MP3 tags",
                "url": "",
            }
        if not has_artist:
            try:
                inferred_artist, artist_count = self._infer_unique_lyrics_artist(
                    metadata.get("title", "")
                )
            except Exception:
                inferred_artist, artist_count = "", 0
            if not inferred_artist:
                if artist_count > 1:
                    status = (
                        "This title belongs to multiple artists, so lyrics "
                        "were not guessed from duration alone. Add [artist "
                        "initials] to identify this song."
                    )
                else:
                    status = (
                        "The artist could not be inferred safely. Add [artist "
                        "initials] or restore the MP3 artist metadata."
                    )
                return {"status": status}
            metadata = dict(metadata)
            metadata["artist_hint"] = inferred_artist
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

    @staticmethod
    def _clean_recognized_title(title):
        """Remove service-added credits while retaining the actual title."""
        title = str(title or "").strip()
        title = re.sub(
            r"\s*[\[(](?:from\s+the|feat(?:uring)?\.?|official\b)[^\])]*[\])]",
            "",
            title,
            flags=re.IGNORECASE,
        )
        return " ".join(title.split()).strip(" -")

    def _recognize_audio_identity(self, name):
        """Identify a local recording through Shazam's audio fingerprint."""
        path = self._song_path(name)
        if not os.path.exists(path):
            return None
        try:
            from shazamio import Shazam
        except ImportError:
            return None

        async def recognize():
            return await Shazam(segment_duration_seconds=10).recognize(path)

        payload = asyncio.run(recognize())
        track = payload.get("track", {}) if isinstance(payload, dict) else {}
        artist = str(track.get("subtitle") or "").strip()
        title = self._clean_recognized_title(track.get("title"))
        if not artist or not title:
            return None
        return {"artist": artist, "title": title, "source": "Shazam"}

    def _remember_recognized_identity(self, name, identity):
        if not name or not isinstance(identity, dict):
            return
        saved = {
            "artist": str(identity.get("artist") or "").strip(),
            "title": str(identity.get("title") or "").strip(),
            "source": str(identity.get("source") or "Shazam"),
        }
        if not saved["artist"] or not saved["title"]:
            return
        entry = self._meta_dict(name)
        if entry.get("recognized_identity") != saved:
            entry["recognized_identity"] = saved
            entry.pop("lyrics_cache", None)
            self._lyrics_memory_cache.pop(name, None)
            self._meta_dirty = True

    @staticmethod
    def _metadata_with_recognized_identity(metadata, identity):
        recognized = dict(metadata)
        recognized["artist"] = identity["artist"]
        recognized["artist_hint"] = identity["artist"]
        recognized["artist_tag"] = ""
        recognized["title"] = identity["title"]
        recognized["identity_source"] = str(
            identity.get("source") or "Shazam"
        )
        return recognized

    def _fetch_preferred_lyrics(self, metadata, force_recognition=False):
        """Identify uncached audio with Shazam, then fetch exact-match lyrics."""
        name = str(metadata.get("library_name") or "").strip()
        already_recognized = metadata.get("identity_source") == "Shazam"
        identity = None
        recognition_error = None

        # A saved fingerprint result is enough: use it to query the lyric
        # providers without decoding or uploading another audio sample.
        if not already_recognized or force_recognition:
            try:
                identity = self._recognize_audio_identity(name)
            except Exception as exc:
                recognition_error = exc

        if identity:
            self._remember_recognized_identity(name, identity)
            metadata = self._metadata_with_recognized_identity(metadata, identity)
            already_recognized = True

        if force_recognition and not identity:
            if recognition_error:
                return {
                    "status": (
                        "Shazam recognition failed: "
                        f"{str(recognition_error).strip() or 'unknown error'}"
                    )
                }
            return {"status": "Shazam could not identify this recording."}

        # If Shazam is unavailable or cannot recognize the recording, retain
        # the lightweight filename/tag path so local and unusual tracks can
        # still obtain embedded or directory lyrics.
        result = self._fetch_directory_lyrics(metadata)
        if result and result.get("text"):
            return result
        if already_recognized:
            return {
                "status": (
                    f"Shazam identified {metadata.get('artist')} — "
                    f"{metadata.get('title')}, but no matching lyrics "
                    "were found."
                )
            }
        return result

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
        artist_tag = str(
            metadata.get("artist_tag") or self._artist_tag_from_filename(
                metadata.get("library_name", "")
            )
        ).strip()
        if not artist and not artist_tag:
            return None
        try:
            duration_ms = int(metadata.get("duration_ms") or 0)
        except (TypeError, ValueError, OverflowError):
            duration_ms = 0
        if duration_ms <= 0:
            duration_ms = self._cached_duration_ms(
                metadata.get("library_name", self.lyrics_song)
            )
        target_seconds = duration_ms / 1000 if duration_ms > 0 else 0
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
        def artist_matches(item):
            found_artist = str(item.get("artistName") or "").strip()
            if artist_tag and not self._artist_matches_tag(found_artist, artist_tag):
                return False
            return bool(found_artist) and (
                not artist or self._lyrics_artist_matches(found_artist, artist)
                or bool(artist_tag)
            )

        if result is not None and (
            not isinstance(result, dict)
            or not self._genius_title_score(result.get("trackName", ""), title)
            or not artist_matches(result)
            or not self._lyrics_duration_matches(result, target_seconds)
        ):
            result = None
        if result is None:
            results = []

            searches = []
            if artist:
                seen_artists = set()
                for query_artist in (
                    artist, *self._lyrics_artist_query_parts(artist)
                ):
                    normalized = self._normalized_lyrics_identity(query_artist)
                    if normalized and normalized not in seen_artists:
                        seen_artists.add(normalized)
                        searches.append({
                            "track_name": title,
                            "artist_name": query_artist,
                        })
            searches.append({"track_name": title})
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
                    if any(
                        self._genius_title_score(item.get("trackName", ""), title)
                        and artist_matches(item)
                        and self._lyrics_duration_matches(item, target_seconds)
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
                and self._lyrics_duration_matches(item, target_seconds)
            ]
            if not candidates:
                return None
            matched_artist = [item for item in candidates if artist_matches(item)]
            if artist or artist_tag:
                candidates = matched_artist
                if not candidates:
                    return None
            if target_seconds > 0:
                candidates.sort(
                    key=lambda item: (
                        not bool(self._lyrics_candidate_duration_seconds(item)),
                        abs(
                            self._lyrics_candidate_duration_seconds(item)
                            - target_seconds
                        ),
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

    def request_lyrics(self, name, force_recognition=False):
        """Load cached lyrics or identify the local song before lookup."""
        if not name or name not in self._all_songs_set:
            self._lyrics_request_id += 1
            self.lyrics_song = ""
            self.lyrics_status = ""
            self.lyrics_text = ""
            self.lyrics_italics = []
            self.lyrics_source = ""
            self.lyrics_source_url = ""
            self._lyrics_loading = False
            return
        if (
            not force_recognition
            and name == self.lyrics_song
            and (self._lyrics_loading or self.lyrics_text)
        ):
            return
        self._lyrics_request_id += 1
        request_id = self._lyrics_request_id
        self.lyrics_song = name
        entry = self.meta.get(name, {})
        saved_identity = (
            entry.get("recognized_identity") if isinstance(entry, dict) else None
        )
        self.lyrics_status = (
            "Finding lyrics…"
            if isinstance(saved_identity, dict)
            and saved_identity.get("artist")
            and saved_identity.get("title")
            and not force_recognition
            else "Recognizing song…"
        )
        self.lyrics_text = ""
        self.lyrics_italics = []
        self.lyrics_source = ""
        self.lyrics_source_url = ""
        self._lyrics_loading = True

        cached = None if force_recognition else self._lyrics_memory_cache.get(name)
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
                result = self._fetch_preferred_lyrics(
                    metadata, force_recognition=force_recognition
                )
            except Exception as exc:
                error = str(exc)
            self._finish_lyrics_request(request_id, name, result, error)

        threading.Thread(target=worker, daemon=True).start()

    def _discard_cached_lyrics(self, names):
        """Forget saved lyrics for songs that no longer exist in the library."""
        names = set(names)
        if not names:
            return False
        changed = False
        memory_cache = getattr(self, "_lyrics_memory_cache", {})
        meta = getattr(self, "meta", {})
        for name in names:
            if memory_cache.pop(name, None) is not None:
                changed = True
            entry = meta.get(name)
            if isinstance(entry, dict) and "lyrics_cache" in entry:
                entry.pop("lyrics_cache", None)
                changed = True
        if getattr(self, "lyrics_song", "") in names:
            # Invalidate an in-flight lookup so it cannot restore lyrics after
            # its MP3 has been deleted.
            self._lyrics_request_id += 1
            self.lyrics_song = ""
            self.lyrics_status = ""
            self.lyrics_text = ""
            self.lyrics_italics = []
            self.lyrics_source = ""
            self.lyrics_source_url = ""
            self._lyrics_loading = False
            changed = True
        return changed

    def _finish_lyrics_request(self, request_id, name, result, error=""):
        if request_id != self._lyrics_request_id or name != self.lyrics_song:
            return
        self._lyrics_loading = False
        if result and result.get("text"):
            clean_text = self._clean_lyrics_text(result.get("text"))
            censored_text = _censor_lyrics_text(clean_text)
            italics = _remap_lyric_italics(
                clean_text, censored_text, result.get("italics") or []
            )
            result = {
                "text": censored_text,
                "source": str(result.get("source") or "Lyrics"),
                "url": str(result.get("url") or ""),
                "italics": italics,
                "cache_version": LYRICS_CACHE_VERSION,
            }
            self._lyrics_memory_cache[name] = result
            self.lyrics_text = result["text"]
            self.lyrics_italics = result["italics"]
            self.lyrics_source = result["source"]
            self.lyrics_source_url = result["url"]
            self.lyrics_status = ""
            entry = self._meta_dict(name)
            entry["lyrics_cache"] = result
            self._meta_dirty = True
        else:
            self.lyrics_status = str(
                (result or {}).get("status")
                or "Lyrics not found for this song. Check its title and artist tags."
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
