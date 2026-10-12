"""Textual widgets, dialogs, and the application shell."""

from player_common import *
from player_engine import Player

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
            """Render the active row without enabling DataTable's own cursor."""
            highlighted = self._music_highlighted_row_key
            if row_index >= 0 and highlighted is not None:
                try:
                    row_key = str(self._row_locations.get_key(row_index).value)
                except Exception:
                    row_key = ""
                if row_key == highlighted:
                    base_style += self.get_component_rich_style("datatable--cursor")
            # The real DataTable cursor can leave an isolated cursor-colored
            # cell beside its scrollbar after scrolling. Playback highlighting
            # is painted through base_style above, so keep that cursor disabled.
            cursor = False
            return super()._render_cell(
                row_index, column_index, base_style, width,
                cursor=cursor, hover=hover,
            )

        def set_music_highlighted_row_key(self, row_key):
            """Change the playback highlight without retaining stale cells."""
            if self._music_highlighted_row_key == row_key:
                return False
            self._music_highlighted_row_key = row_key
            # Our playback key is deliberately independent of DataTable's
            # navigation cursor, so it isn't part of Textual's line-cache key.
            # Clear both line and cell caches before repainting; otherwise a
            # fragment of the previous black row can survive an incremental
            # update as a single dark character or cell.
            self._clear_caches()
            self.refresh()
            return True

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

        def _filled_cells(self):
            width = max(1, self.size.width)
            ratio = min(1.0, self.elapsed / self.duration) if self.duration else 0.0
            return min(width, max(0, round(width * ratio)))

        def set_progress(self, elapsed, duration):
            old_filled = self._filled_cells()
            old_has_duration = self.duration > 0
            self.elapsed = max(0.0, float(elapsed or 0))
            self.duration = max(0.0, float(duration or 0))
            # A long track often occupies the same terminal cell for several
            # seconds. Avoid repainting an identical bar on every timer tick.
            if (
                self._filled_cells() != old_filled
                or (self.duration > 0) != old_has_duration
            ):
                self.refresh()

        def render(self):
            width = max(1, self.size.width)
            filled = self._filled_cells()
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
            ("Click / Enter", "Play an MP3 or preview a YouTube video or playlist"),
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
        #library { width: 1fr; height: 100%; border: solid #777777; border-left: none; }
        #lyrics-tab { width: 3; min-width: 3; max-width: 3; height: 8; margin-top: 1; padding: 0; border: none; background: #eeeeea; color: #222222; content-align: center middle; text-style: bold; }
        #workspace.lyrics-open #lyrics-tab { display: none; }
        #workspace.youtube-preview #lyrics-tab { display: none; }
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
        #lyrics-content { width: 100%; height: auto; padding-right: 1; }
        #workspace.youtube #playlists { display: none; }
        #workspace.youtube #playlist-resizer { display: none; }
        #workspace.youtube #library { border-left: solid #777777; }
        #table {
            height: 1fr;
            scrollbar-size: 2 1;
            scrollbar-color: #aaaaaa;
            scrollbar-color-hover: #888888;
            scrollbar-color-active: #777777;
            scrollbar-background: #fafaf7;
            scrollbar-background-hover: #f1f1ed;
            scrollbar-background-active: #eeeeea;
            scrollbar-corner-color: #fafaf7;
        }
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
            self._lyrics_scrolled_token = None
            self._lyrics_enabled = False
            self._lyrics_width = None
            self._playlist_width = None
            self._lyrics_render_state = None
            self._playlist_sync_state = None
            self._tick_timer = None

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
                        yield Button("◀", id="previous-track")
                        yield Button("Pause", id="play-pause")
                        yield Button("▶", id="next-track")
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
            self._tick_timer = self.set_interval(TEXTUAL_TICK_S, self.tick)
            # Start the output-safety listener now; the setup runs in a
            # background thread and does not delay the first screen paint.
            self.player.start_background_services()

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
            tab_width = 0 if (
                workspace.has_class("lyrics-open")
                or workspace.has_class("youtube-preview")
            ) else 3
            available = workspace.content_region.width - lyrics_width - tab_width - 1 - 30
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
            preview_active = self.query_one("#workspace", Horizontal).has_class(
                "youtube-preview"
            )
            lyrics_visible = self._lyrics_enabled and not preview_active
            reserved_lyrics = 9 if lyrics_visible else 0
            tab_width = 0 if lyrics_visible or preview_active else 3
            available = self._workspace_width_for_terminal() - tab_width - 1 - 16 - reserved_lyrics
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
                8, self._workspace_width_for_terminal() - left_width - 1 - 16
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
            table._music_row_signatures = {}
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
            changed = False
            if schema == "youtube":
                for key, title in (
                    ("title", "Title"), ("channel", "Channel"),
                    ("views", "Views"), ("likes", "Likes"),
                ):
                    column = table.columns.get(key)
                    if column is not None:
                        label = self._youtube_sort_heading(title, key)
                        if str(column.label) != label:
                            column.label = Text(label)
                            changed = True
                if changed:
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
                    label = self._sort_heading(title, key)
                    if str(column.label) != label:
                        column.label = Text(label)
                        changed = True
            if changed:
                table.refresh()

        def tick(self):
            if not self.player or not self.is_mounted:
                return
            self.player._process_pending()
            self.player._queue_apple_radio_poll()
            self.player._save_session_if_due()
            duration_updates = self.player._take_duration_ui_updates()
            if self.player.dirty:
                self.refresh_ui(rebuild_table=True)
            else:
                # Keep the clock isolated from the much more expensive table
                # and layout refresh. This callback runs once per second.
                self._sync_lyrics_panel()
                self.refresh_transport()
                self._refresh_duration_cells(duration_updates)

        def _sync_lyrics_panel(self):
            """Update lyrics only while the user has opened the sidebar."""
            p = self.player
            if not p:
                return
            preview_active = p._youtube_loading or p._is_youtube_preview_current()
            name = (
                p.current if not preview_active and p.current in p._all_songs_set
                else ""
            )
            token = (name, p._playback_id) if name else None
            workspace = self.query_one("#workspace", Horizontal)
            workspace.set_class(preview_active, "youtube-preview")
            if token != self._lyrics_token:
                self._lyrics_token = token
            visible = self._lyrics_enabled and not preview_active
            visibility_changed = workspace.has_class("lyrics-open") != visible
            workspace.set_class(visible, "lyrics-open")
            if visibility_changed:
                self.call_after_refresh(self._clamp_lyrics_sidebar_width)
            if not visible:
                if preview_active and self._lyrics_requested_token is not None:
                    self._lyrics_requested_token = None
                    self._lyrics_scrolled_token = None
                    p.request_lyrics("")
                return
            reset_scroll = token != self._lyrics_requested_token or visibility_changed
            if token != self._lyrics_requested_token:
                self._lyrics_requested_token = token
                self._lyrics_scrolled_token = None
                p.request_lyrics(name)
            render_state = (
                name,
                p.lyrics_source,
                p.lyrics_source_url,
                p.lyrics_text,
                p.lyrics_status,
                id(p.lyrics_italics),
            )
            if render_state == self._lyrics_render_state:
                return
            self._lyrics_render_state = render_state
            self.query_one("#lyrics-track", Static).update(
                Text(name or "No local song playing")
            )
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
            formatted = (
                _render_singer_lyrics(content, p.lyrics_italics)
                if p.lyrics_text
                else Text(content)
            )
            self.query_one("#lyrics-content", Static).update(formatted)
            if token and p.lyrics_text and token != self._lyrics_scrolled_token:
                self._lyrics_scrolled_token = token
                reset_scroll = True
            if reset_scroll:
                self.call_after_refresh(
                    lambda: self.query_one("#lyrics-scroll", VerticalScroll).scroll_to(
                        y=0, animate=False, force=True
                    )
                )

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
            row_signatures = getattr(table, "_music_row_signatures", {})
            for name in names:
                if name not in existing_names:
                    continue
                duration_ms = p._duration_ms_cache.get(name, 0)
                duration_text = (
                    p._fmt_time(duration_ms) if duration_ms > 0 else "--:--"
                )
                listen_text = p._fmt_listen_hours(p._total_listen_hours(name))
                table.update_cell(
                    name,
                    "duration",
                    Text(duration_text, justify="right"),
                )
                table.update_cell(
                    name,
                    "listen",
                    Text(listen_text, justify="right"),
                )
                previous = row_signatures.get(name)
                if previous is not None and len(previous) == 7:
                    updated = list(previous)
                    updated[3] = listen_text
                    updated[5] = duration_text
                    row_signatures[name] = tuple(updated)

        def refresh_playlists(self):
            if not self.player:
                return
            box = self.query_one("#playlist-buttons", Vertical)
            box.remove_children()
            self._playlist_sync_state = None
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
            marker_visible = self.player.current in self.player._all_songs_set
            state = (active, source, marker_visible, tuple(self.playlist_ids.items()))
            if state == self._playlist_sync_state:
                return
            self._playlist_sync_state = state
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

        @staticmethod
        def _update_if_changed(widget, value, signature=None):
            """Update a Textual widget only when its displayed value changed."""
            signature = value if signature is None else signature
            if getattr(widget, "_music_content_signature", object()) == signature:
                return False
            widget._music_content_signature = signature
            widget.update(value)
            return True

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
                time_text = "  ".join(details)
                progress = (percent, 100)
            elif p.apple_radio_enabled:
                time_text = (
                    "Playing through Apple Music" if p.apple_radio_active
                    else "Select a station below"
                )
                progress = (0, 0)
            else:
                time_text = f"{p._fmt_time(elapsed)} / {p._fmt_time(duration)}"
                progress = (elapsed, duration)
            self._update_if_changed(
                self.query_one("#time-label", Static), time_text
            )
            self.query_one("#progress", DurationBar).set_progress(*progress)

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
            self._update_if_changed(
                self.query_one("#now", Static), now_content, str(now_content)
            )
            stats_text = p._library_stats_label()
            self._update_if_changed(
                self.query_one("#stats", Static), stats_text
            )
            transport_visible = (
                not p.apple_radio_enabled
                and p.current not in ("None", "Loading...")
            )
            play_label = "Resume" if p.paused else "Pause"
            play_button = self.query_one("#play-pause", Button)
            play_button.display = transport_visible
            skip_visible = (
                p.youtube_preview_enabled
                and p._youtube_playlist_total > 0
            )
            previous_button = self.query_one("#previous-track", Button)
            next_button = self.query_one("#next-track", Button)
            previous_button.display = skip_visible
            next_button.display = skip_visible
            # Arrow-only Textual buttons need a little horizontal breathing
            # room. At three cells the two border cells crowd the glyph and
            # some terminals render it as if it were touching the wall.
            self._fit_button_to_label(previous_button, frame_cells=4)
            self._fit_button_to_label(next_button, frame_cells=4)
            if str(play_button.label) != play_label:
                play_button.label = play_label
                play_button.refresh(layout=True)
            # The word label remains legible with only its two border cells;
            # keeping this compact leaves room for both full arrow buttons.
            self._fit_button_to_label(play_button, frame_cells=2)
            level = min(10, max(0, round(p.vol * 10)))
            volume_text = (
                f"{'█' * level}{'·' * (10 - level)}  {round(p.vol * 100):>3}%"
            )
            self._update_if_changed(
                self.query_one("#volume-label", Static), volume_text
            )
            mode_button = self.query_one("#mode", Button)
            mode_label = "Download" if p.youtube_preview_enabled else f"Mode: {p.play_mode}"
            mode_button.display = True
            if str(mode_button.label) != mode_label:
                mode_button.label = mode_label
                mode_button.refresh(layout=True)
            self._fit_button_to_label(mode_button)
            safety_button = self.query_one("#non-speaker-toggle", Button)
            safety_label = "Yes" if p.non_speaker_mode else "No"
            if str(safety_button.label) != safety_label:
                safety_button.label = safety_label
            self._fit_button_to_label(safety_button)
            tags_button = self.query_one("#show-tags-toggle", Button)
            tags_label = "Yes" if p.show_tags else "No"
            if str(tags_button.label) != tags_label:
                tags_button.label = tags_label
            self._fit_button_to_label(tags_button)
            for selector in (
                "#vol-down", "#vol-up", "#youtube-back", "#youtube-stop",
                "#yt-preview", "#apple-radio", "#cancel", "#help-link",
            ):
                self._fit_button_to_label(self.query_one(selector, Button))
            workspace = self.query_one("#workspace", Horizontal)
            workspace.set_class(p.youtube_preview_enabled, "youtube")
            radio_button = self.query_one("#apple-radio", Button)
            if str(radio_button.label) != "Apple Radio":
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
                self._update_if_changed(editor_label, "Search")
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
                # SongTable paints the active media row itself. Keep Textual's
                # navigation cursor hidden and parked at row 0 so it can't
                # leave a stray dark cell or drag sorting back to the song.
                table.show_cursor = False
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
                    table.set_music_highlighted_row_key(active_row_key)
                    expected_keys = [f"youtube-{index}" for index in range(len(p.youtube_results))]
                    existing_keys = [str(row.key.value) for row in table.ordered_rows]
                    update_in_place = existing_keys == expected_keys
                    if not update_in_place:
                        table.clear()
                        table._music_row_signatures = {}
                    row_signatures = getattr(table, "_music_row_signatures", {})
                    for index, result in enumerate(p.youtube_results, 1):
                        is_channel = p._is_youtube_channel_result(result)
                        is_playlist = p._is_youtube_playlist_result(result)
                        title = str(result.get("title") or "Untitled")
                        if is_channel:
                            title = f"[CHANNEL] {title}"
                        elif is_playlist:
                            title = f"[PLAYLIST] {title}"
                        title = p._truncate(
                            title, table.columns.get("title").width
                        )
                        channel = p._truncate(
                            str(result.get("channel") or "--"),
                            table.columns.get("channel").width,
                        )
                        style = "bold" if (is_channel or is_playlist) else ""
                        view_text = (
                            "channel" if is_channel
                            else (
                                f"{result.get('count')} tracks"
                                if is_playlist and result.get("count")
                                else "playlist" if is_playlist
                                else p._fmt_yt_count(result.get("views"))
                            )
                        )
                        like_text = (
                            "open" if is_channel
                            else "preview" if is_playlist
                            else p._fmt_yt_count(result.get("likes"))
                        )
                        signature = (
                            str(index), title, channel, view_text, like_text, style
                        )
                        row_key = f"youtube-{index - 1}"
                        previous = row_signatures.get(row_key)
                        if update_in_place and previous == signature:
                            continue
                        values = (
                            Text(str(index), style=style, justify="left"),
                            Text(title, style=style),
                            Text(channel, style=style),
                            Text(view_text, style=style, justify="right"),
                            Text(like_text, style=style, justify="right"),
                        )
                        if update_in_place:
                            style_changed = (
                                previous is not None and previous[5] != style
                            )
                            for value_index, (column, value) in enumerate(zip(
                                ("number", "title", "channel", "views", "likes"),
                                values,
                            )):
                                if (
                                    previous is None
                                    or style_changed
                                    or previous[value_index] != signature[value_index]
                                ):
                                    table.update_cell(row_key, column, value)
                        else:
                            table.add_row(*values, key=row_key)
                        row_signatures[row_key] = signature
                    table._music_row_signatures = row_signatures
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
                    table._music_row_signatures = {}
                row_signatures = getattr(table, "_music_row_signatures", {})
                active_row_key = p.current if p.current in p.songs else None
                table.set_music_highlighted_row_key(active_row_key)
                for index, name in enumerate(p.songs, 1):
                    duration_ms = p._cached_duration_ms(name)
                    plain_values = (
                        "●" if name in p.selected_songs else "○",
                        str(index),
                        p._truncate_song_name(
                            name if p.show_tags else p._without_artist_tag(name),
                            table.columns.get("name").width,
                        ),
                        p._fmt_listen_hours(p._total_listen_hours(name)),
                        str(p._plays(name)),
                        p._fmt_time(duration_ms) if duration_ms > 0 else "--:--",
                        p._fmt_since_last_play(name),
                    )
                    previous = row_signatures.get(name)
                    if update_in_place and previous == plain_values:
                        continue
                    def cell(value, justify="left"):
                        return Text(str(value), justify=justify)
                    values = (
                        cell(plain_values[0]),
                        cell(plain_values[1], "left"),
                        cell(plain_values[2]),
                        cell(plain_values[3], "right"),
                        cell(plain_values[4], "right"),
                        cell(plain_values[5], "right"),
                        cell(plain_values[6], "right"),
                    )
                    if update_in_place:
                        for value_index, (column, value) in enumerate(zip(
                            ("select", "number", "name", "listen", "plays", "duration", "last"),
                            values,
                        )):
                            if previous is None or previous[value_index] != plain_values[value_index]:
                                table.update_cell(name, column, value)
                    else:
                        table.add_row(*values, key=name)
                    row_signatures[name] = plain_values
                table._music_row_signatures = row_signatures
                if not update_in_place:
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
            if p._is_youtube_playlist_result(result):
                p._start_youtube_playlist_preview(result)
                self.refresh_ui(rebuild_table=True)
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
                elif self.player._is_youtube_playlist_result(result):
                    self.player._start_youtube_playlist_preview(result)
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
            elif button_id == "previous-track":
                p._navigate_youtube_playlist(-1)
            elif button_id == "play-pause": p.toggle_pause()
            elif button_id == "next-track":
                p._navigate_youtube_playlist(1)
            elif button_id == "mode":
                if p.youtube_preview_enabled: self.begin_youtube_download()
                else: p._switch_play_mode()
            elif button_id == "non-speaker-toggle":
                p.non_speaker_mode = not p.non_speaker_mode
                p._output_safety_wakeup.set()
                if not p.non_speaker_mode:
                    p._paused_by_speaker_safety = False
                p._apply_volume(force=True)
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
            if self._tick_timer is not None:
                self._tick_timer.stop()
                self._tick_timer = None
            if self.player:
                self.player._ui_wakeup = None
                self.player._shutdown_audio()

__all__ = [name for name in globals() if not name.startswith("__")]
