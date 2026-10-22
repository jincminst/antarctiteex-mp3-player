# antarctiteex-mp3-player

A terminal interface for playing and organizing a folder of MP3 files.

## Features

- Local MP3 Playback
- Automatic Lyrics Sidebar with Genius, LRCLIB, and Audio Recognition
- Library Search and Filtering
- Playlist Creation, Renaming, and Deletion
- Automatic Artist-Tag Playlists with Manual Drag-and-Drop Fallback
- Optional Artist-Tag Display in the MP3 List
- Drag-and-Drop Playlist Management
- Shift-Click Range Selection and Multi-Song Actions
- Loop and Weighted Shuffle Playback
- Previous and Next Controls for YouTube Playlist Previews
- Play Count, Listening Time, Duration, and Last-Played Tracking
- Library Sorting by Name, Play Time, Plays, Duration, or Last Played
- YouTube Video, Channel, and Playlist Search and Preview
- Channel Browsing with Both Video and Playlist Rows
- YouTube Audio Downloads with Pre-Save Renaming and Live Progress
- Sequential YouTube Playlist Previews with Automatic Temporary-File Cleanup
- YouTube Sorting by Title, Channel, Views, or Likes
- Apple Music Radio with an Upward-Opening Station Menu (macOS)
- Play, Pause, Seek, and Volume Controls
- Paused Session Restore for the Last Song, Position, Volume, Playlist, and Mode
- One-Click Library Refresh for Files Added or Removed Outside the App
- Automatic Per-Song Volume Normalization
- Keyboard, Mouse, and Media-Key Controls
- Compact In-App Control Reference
- In-App Success, Warning, and Error Notifications
- Detailed MP3 Playback-Failure Notifications
- MP3 Renaming and Trash-Safe Deletion
- Multi-Song Selection and Bulk Actions
- Automatic Pause for Screen Lock and Find My Alerts (macOS)
- Event-Driven Non-Speaker Safety Mode (macOS)
- MP3 Filename Export
- Responsive Terminal Interface
- Unified Song, Playlist, and Analysis Cache

## Install

Install the player from PyPI:

```sh
python3 -m pip install antarctiteex-mp3-player
```

Then open a music folder and launch the player:

```sh
cd /path/to/music
antarctiteex
```

You can also pass the music folder directly:

```sh
antarctiteex /path/to/music
```

The `mp3-player` command is also available as an alias.

On macOS, Non-Speaker Mode listens for output-device changes through CoreAudio
and uses `SwitchAudioSource` to identify the new output. It pauses playback as
soon as the output changes, then resumes it if the new output is safe.

The player stores song metadata, playlists, settings, and analysis in a single
`.mp3cache.json` file inside the selected music folder.

## Lyrics

Click the vertical **Lyrics** tab beside the song list to open the scrollable
sidebar. Lyrics are looked up only while that sidebar is open. For an uncached
song, the player uses a short Shazam audio fingerprint, caches the recognized
title and artist, then searches Genius and LRCLIB with that identity. Embedded
lyrics and filename metadata remain fallbacks when Shazam cannot identify a
recording. Non-Latin titles remain intact during matching, and soundtrack
annotations from Shazam are removed before lookup. Provider credits may include
both a native name and a Latin alias; the player compares those aliases as the
same artist even when providers use a minor spelling variation. Genius's
official API returns song metadata and page URLs, not
lyrics text. Set `GENIUS_ACCESS_TOKEN` to a Genius API client access token to
use its official search endpoint; without a token, the player uses Genius's
public website search. Successful results are saved in `.mp3cache.json`, so
replaying a song does not make another network request. Section headings
supplied by the source, such as `[Verse]`, `[Chorus]`, and `[Bridge]`, remain
visible. The lookup prefers a verified Genius English Translation page for
every song and falls back to the canonical page when no English translation
exists. Other translation languages are rejected. Genius's own singer
formatting is translated directly for terminals:
normal lyrics keep the default text color, italic spans use one accent, and
bold spans use another. The player does not infer singers from word counts or
punctuation. Lyrics are censored
with better-profanity,
including disguised words such as `s*x`, `s✱x`, and `s*xy`; "hell" and "God"
stay visible.
The source row becomes a dropdown when Genius has both an original page and a
verified English translation. Your choice is saved for that MP3, and choosing
original or English also becomes the default for other songs that offer both.
Drag the sidebar's left edge to resize it. Playlist and lyrics widths are saved
in `.mp3cache.json` and restored the next time you open the player. Resizing
lyrics leaves the playlist width alone.

When lyrics are present, select **[Not the right lyrics?]** at the bottom of
the sidebar to bypass the cached result, identify the recording through Shazam,
and fetch the lyrics again with the recognized title and artist. Shazam
identifies the released recording from its audio fingerprint; it does not need
to hear every credited singer's verse. Recognition runs in the background and
its identity is cached; no local AI model is stored.
When an original and a remix have the same title and primary artist, the full
Genius recording credits are compared with the Shazam identity so remix-only
performers do not make the wrong page win.
If Shazam itself identifies the wrong release and its credits and duration are
also ambiguous, the player cannot reliably infer an event in audio it did not
inspect.

Accurate artist and title metadata gives the best matches. The player reads
MP3 tags first. If those are missing, use `[Artist] Song Title.mp3` or
`Artist - Song Title.mp3`. Close the sidebar with its `×` button or the
vertical Lyrics tab.

For abbreviated artist tags, an artist-named playlist with the same tag can
identify the artist. If no playlist exists, the player can infer the artist
from a clearer song with the same tag, such as using `[AG] 7 rings` to identify
`[AG] freak`. Verified artist hints are saved in `.mp3cache.json` for later songs.

## Artist Tags and Automatic Playlists

Artist tags are short labels at the very beginning of an MP3 filename. Put the
tag in square brackets, followed by the song title:

```text
[OR] vampire.mp3
[OR] drivers license.mp3
[21P] Stressed Out.mp3
```

To make an automatic playlist, create a playlist, right-click it, choose
**Edit tag**, and enter only the text inside the brackets. For example, enter
`OR`, not `[OR]`. The player then fills that playlist with every MP3 whose
filename begins with `[OR]`. Matching is case-insensitive, so `[or]` also
matches. The tag must be at the start of the filename.

A tagged playlist updates automatically when matching MP3 files are added,
renamed, or removed. You can also drag any other MP3 into it; manually added
songs appear alongside the automatic tag matches and can be removed normally.
Removing an automatic match requires changing its filename tag or clearing the
playlist's tag.

Playlist names do not display their assigned tags in the sidebar. The
**Show Tags** control only changes how MP3 names appear in the song list; it
does not rename files or affect automatic playlist matching.

For lyrics, the bracketed tag is checked against the artist's initials before
online results are used. Numbers can stand for their spelled-out words: `[21P]`
matches Twenty One Pilots (`TOP`). Naming a tagged playlist after the artist
can also help the search find the right song. The lyrics search tries the
number as English words too, so `[21P]` also searches with `twenty one P`.
Joined artist names work too: `[1R] Counting Stars` matches OneRepublic.
If searches using the tag miss, the player also searches by song title and
accepts lyrics only when the result's artist matches the tag.
In Genius lyrics, the terminal colors the exact singer names and lyric spans
that Genius italicizes or bolds. Unstyled text keeps the normal terminal color.

`ffmpeg` and `ffprobe` must be available on `PATH` for audio conversion and
analysis features. On macOS, install them with `brew install ffmpeg`.

Full YouTube support also needs an external JavaScript runtime. The player
automatically uses an installed Deno, Node, QuickJS (`qjs`), or Bun executable
and passes its absolute path to yt-dlp. The package installs yt-dlp's matching
EJS scripts through the `yt-dlp[default]` dependency group.

## Development

The source is split by responsibility so changes stay localized:

- `play.py`: public imports and command-line entry point
- `player_engine.py`: player construction and service startup
- `player_library.py`: playlists, selection, sorting, and shuffle weights
- `player_data.py`: cache, metadata, and lyrics lookup
- `player_audio.py`: duration, volume analysis, and listening history
- `player_system.py`: macOS media keys and output safety
- `player_online.py`: YouTube and Apple Music radio
- `player_runtime.py`: playback, pending work, drawing, and legacy input
- `player_ui.py`: Textual widgets, screens, layout, and events
- `player_common.py`: shared imports, constants, and lyrics rendering helpers
- `player_legacy_ui.py`: remaining curses-compatible layout and dialog helpers

Run the test suite with:

```sh
python3 -m unittest test_play.py
```
