import argparse
import asyncio
import ctypes
import difflib
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

profanity.load_censor_words(whitelist_words=["hell", "god"])


def _fill_censor_word(template, index, letter):
    return template[:index] + letter + template[index + 1:]


profanity.add_censor_words([
    _fill_censor_word("s xy", 1, "e"),
    _fill_censor_word("s xier", 1, "e"),
    _fill_censor_word("s xiest", 1, "e"),
    _fill_censor_word("s xiness", 1, "e"),
    _fill_censor_word("s xting", 1, "e"),
])
_MASKED_CENSOR_WORDS = {}
for _word in profanity.CENSOR_WORDSET:
    _spelling = str(_word).lower()
    if _spelling.isalpha():
        _MASKED_CENSOR_WORDS.setdefault(len(_spelling), set()).add(_spelling)

_MASKED_WORD = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[\*✱✲✳✴✵✶✷✸✹✺✻✼✽✾•·_\-–—!]+)+[A-Za-z0-9]+['’]?(?![A-Za-z0-9])"
)
_TRAILING_APOSTROPHE_WORD = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]+['’](?![A-Za-z0-9])")
_MASKED_LETTERS = str.maketrans({
    "✱": "*", "✲": "*", "✳": "*", "✴": "*", "✵": "*", "✶": "*",
    "✷": "*", "✸": "*", "✹": "*", "✺": "*", "✻": "*", "✼": "*",
    "✽": "*", "✾": "*", "•": "*", "·": "*", "_": "*",
    "-": "*", "–": "*", "—": "*", "!": "i",
})
_PROFANITY_HOMOGLYPHS = str.maketrans({
    # Genius lyrics occasionally contain Cyrillic or Greek lookalikes copied
    # from stylized text.  Normalize them only while checking a word so the
    # displayed lyrics and legitimate non-English text are not rewritten.
    "а": "a", "А": "A", "е": "e", "Е": "E", "і": "i", "І": "I",
    "ј": "j", "Ј": "J", "о": "o", "О": "O", "р": "p", "Р": "P",
    "с": "c", "С": "C", "ѕ": "s", "Ѕ": "S", "х": "x", "Х": "X",
    "у": "y", "У": "Y",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X", "α": "a", "β": "b", "ε": "e", "ι": "i",
    "κ": "k", "ο": "o", "ρ": "p", "τ": "t", "υ": "y", "χ": "x",
})
_HOMOGLYPH_WORD = re.compile(
    r"(?<!\w)[^\W\d_]+(?:['’][^\W\d_]+)?['’]?(?!\w)", re.UNICODE
)


def _censor_lyrics_text(value):
    """Mask profanity, including common lyric spellings with substitute glyphs."""
    def censor_masked(match):
        normalized = match.group().rstrip("'’").translate(_MASKED_LETTERS)
        if profanity.censor(normalized) == "****":
            return "****"
        # The library recognizes some asterisks, but not several missing
        # letters in one word (for example m*th*rf**ker).
        if "*" in normalized:
            letters = normalized.lower()
            fixed = sum(character != "*" for character in letters)
            if fixed >= 4 and fixed * 2 >= len(letters):
                for word in _MASKED_CENSOR_WORDS.get(len(letters), ()):
                    if all(a == "*" or a == b for a, b in zip(letters, word)):
                        return "****"
        return match.group()

    def censor_homoglyphs(match):
        original = match.group()
        normalized = original.translate(_PROFANITY_HOMOGLYPHS)
        if normalized != original and profanity.censor(normalized) != normalized:
            return "****"
        return original

    value = _MASKED_WORD.sub(censor_masked, value)
    value = _TRAILING_APOSTROPHE_WORD.sub(censor_masked, value)
    value = _HOMOGLYPH_WORD.sub(censor_homoglyphs, value)
    return profanity.censor(value)

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
# Persist the exact position on clean shutdown.  During playback, a longer
# checkpoint avoids rewriting (and fsyncing) the unified cache every few
# seconds while still keeping crash recovery reasonably current.
SESSION_SAVE_INTERVAL_S = 15.0
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
# CoreAudio wakes the watcher immediately on a route change. These are only
# backstops for a missed native event, so keep process-based device checks
# infrequent; the no-listener fallback below remains intentionally fast.
OUTPUT_SAFETY_ACTIVE_POLL_S = 30.0
OUTPUT_SAFETY_IDLE_POLL_S = 60.0
OUTPUT_SAFETY_FALLBACK_POLL_S = 0.5
APPLE_RADIO_POLL_S = 5.0
YT_PREVIEW_AUDIO_QUALITY = "0"
YT_PREVIEW_FORMAT = "ba[ext=m4a]/ba[ext=mp3]/ba[ext=ogg]/ba[acodec^=mp4a]/ba[acodec^=opus]/ba/b"
YT_DLP_CONCURRENT_FRAGMENTS = "4"
YT_DLP_RETRIES = "2"
YT_DLP_COOKIES_FROM_BROWSER = "edge"
YT_DLP_JS_RUNTIMES = (
    ("deno", "deno"),
    ("node", "node"),
    ("quickjs", "qjs"),
    ("bun", "bun"),
)
YT_DLP_IMPERSONATE = "chrome"
YT_DOWNLOAD_FORMAT = "ba/b"
YT_STATS_WORKERS = 3
YT_SEARCH_RESULT_LIMIT = 30
YT_SEARCH_CHANNEL_LIMIT = 5
YT_SEARCH_PLAYLIST_LIMIT = 6
YT_SEARCH_TIMEOUT_S = 15.0
LYRICS_API_URL = "https://lrclib.net/api"
GENIUS_SEARCH_URL = "https://genius.com/api/search/multi"
GENIUS_OFFICIAL_SEARCH_URL = "https://api.genius.com/search"
LYRICS_HTTP_TIMEOUT_S = 8
LYRICS_CACHE_VERSION = 9
_ITALIC_START = "\ue000"
_ITALIC_END = "\ue001"
_BOLD_START = "\ue002"
_BOLD_END = "\ue003"
_SINGER_COLORS = ("#61c9ff", "#ffba6b")


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("selector", ctypes.c_uint32),
        ("scope", ctypes.c_uint32),
        ("element", ctypes.c_uint32),
    ]


_AUDIO_PROPERTY_LISTENER = ctypes.CFUNCTYPE(
    ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32,
    ctypes.POINTER(_AudioObjectPropertyAddress), ctypes.c_void_p,
)
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
        self.italic_depth = 0
        self.bold_depth = 0
        self.span_depth = 0
        self.italic_span_depths = []
        self.bold_span_depths = []

    def _enter_italic(self):
        if not self.italic_depth:
            self.lines.append(_ITALIC_START)
        self.italic_depth += 1

    def _leave_italic(self):
        if self.italic_depth:
            self.italic_depth -= 1
            if not self.italic_depth:
                self.lines.append(_ITALIC_END)

    def _enter_bold(self):
        if not self.bold_depth:
            self.lines.append(_BOLD_START)
        self.bold_depth += 1

    def _leave_bold(self):
        if self.bold_depth:
            self.bold_depth -= 1
            if not self.bold_depth:
                self.lines.append(_BOLD_END)

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
        elif tag in {"i", "em"}:
            self._enter_italic()
        elif tag in {"b", "strong"}:
            self._enter_bold()
        elif tag == "span":
            self.span_depth += 1
            style = attributes.get("style", "")
            if re.search(r"font-style\s*:\s*italic\b", style, re.I):
                self.italic_span_depths.append(self.span_depth)
                self._enter_italic()
            if re.search(r"font-weight\s*:\s*(?:bold|[6-9]00)\b", style, re.I):
                self.bold_span_depths.append(self.span_depth)
                self._enter_bold()

    def handle_endtag(self, tag):
        if self.depth and tag == "div":
            self.depth -= 1
            if not self.depth:
                if self.italic_depth:
                    self.lines.append(_ITALIC_END)
                    self.italic_depth = 0
                if self.bold_depth:
                    self.lines.append(_BOLD_END)
                    self.bold_depth = 0
                self.span_depth = 0
                self.italic_span_depths.clear()
                self.bold_span_depths.clear()
                self.lines.append("\n")
        elif self.depth and tag in {"i", "em"} and self.italic_depth:
            self._leave_italic()
        elif self.depth and tag in {"b", "strong"} and self.bold_depth:
            self._leave_bold()
        elif self.depth and tag == "span":
            if self.italic_span_depths and self.italic_span_depths[-1] == self.span_depth:
                self.italic_span_depths.pop()
                self._leave_italic()
            if self.bold_span_depths and self.bold_span_depths[-1] == self.span_depth:
                self.bold_span_depths.pop()
                self._leave_bold()
            self.span_depth = max(0, self.span_depth - 1)

    def handle_data(self, data):
        if self.depth:
            self.lines.append(data)

    def text(self):
        return self.marked_text().translate({
            ord(_ITALIC_START): None,
            ord(_ITALIC_END): None,
            ord(_BOLD_START): None,
            ord(_BOLD_END): None,
        })

    def marked_text(self):
        return "".join(self.lines)


def _extract_lyric_styles(marked_text):
    """Return plain lyrics plus Genius italic and bold character ranges."""
    plain_lines = []
    italic_lines = []
    bold_lines = []
    italic = False
    bold = False
    for marked_line in marked_text.split("\n"):
        plain = []
        italic_spans = []
        bold_spans = []
        italic_start = 0 if italic else None
        bold_start = 0 if bold else None
        for character in marked_line:
            if character == _ITALIC_START:
                if not italic:
                    italic_start = len(plain)
                italic = True
            elif character == _ITALIC_END:
                if italic and italic_start is not None and len(plain) > italic_start:
                    italic_spans.append([italic_start, len(plain)])
                italic = False
                italic_start = None
            elif character == _BOLD_START:
                if not bold:
                    bold_start = len(plain)
                bold = True
            elif character == _BOLD_END:
                if bold and bold_start is not None and len(plain) > bold_start:
                    bold_spans.append([bold_start, len(plain)])
                bold = False
                bold_start = None
            else:
                plain.append(character)
        if italic and italic_start is not None and len(plain) > italic_start:
            italic_spans.append([italic_start, len(plain)])
        if bold and bold_start is not None and len(plain) > bold_start:
            bold_spans.append([bold_start, len(plain)])
        plain_lines.append("".join(plain))
        italic_lines.append(italic_spans)
        bold_lines.append(bold_spans)
    return "\n".join(plain_lines), italic_lines, bold_lines


def _extract_lyric_italics(marked_text):
    """Backward-compatible italic-only view of Genius formatting."""
    lyrics, italics, _bold = _extract_lyric_styles(marked_text)
    return lyrics, italics


def _remap_lyric_italics(original, censored, italics):
    """Keep italic ranges on the same words when censorship changes lengths."""
    original_lines = original.split("\n")
    censored_lines = censored.split("\n")
    if len(original_lines) != len(censored_lines):
        return []
    remapped = []
    for index, before in enumerate(original_lines):
        after = censored_lines[index]
        marked = [False] * len(before)
        for start, finish in (italics[index] if index < len(italics) else []):
            for position in range(max(0, start), min(len(before), finish)):
                marked[position] = True
        converted = [False] * len(after)
        for operation, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, before, after, autojunk=False
        ).get_opcodes():
            if operation == "equal":
                converted[j1:j2] = marked[i1:i2]
            elif operation == "replace" and i1 < i2 and all(marked[i1:i2]):
                converted[j1:j2] = [True] * (j2 - j1)
            elif operation == "insert" and i1 > 0 and i1 < len(marked):
                if marked[i1 - 1] and marked[i1]:
                    converted[j1:j2] = [True] * (j2 - j1)
        spans = []
        start = None
        for position, is_marked in enumerate(converted + [False]):
            if is_marked and start is None:
                start = position
            elif not is_marked and start is not None:
                spans.append([start, position])
                start = None
        remapped.append(spans)
    return remapped


def _render_singer_lyrics(lyrics, italics, bold=None):
    """Translate Genius singer formatting into terminal-safe colors."""
    rendered = Text(lyrics)
    lines = lyrics.split("\n")
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line) + 1

    for spans_by_line, color in (
        (italics or [], _SINGER_COLORS[0]),
        (bold or [], _SINGER_COLORS[1]),
    ):
        for line_index, spans in enumerate(spans_by_line):
            if line_index >= len(lines):
                break
            for start, finish in spans:
                start = max(0, min(len(lines[line_index]), start))
                finish = max(start, min(len(lines[line_index]), finish))
                if finish > start:
                    rendered.stylize(
                        color,
                        offsets[line_index] + start,
                        offsets[line_index] + finish,
                    )
    return rendered


# Re-export private helpers as well: play.py has historically exposed them.
__all__ = [name for name in globals() if not name.startswith("__")]
