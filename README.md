# antarctiteex-mp3-player

A terminal interface for playing and organizing a folder of MP3 files.

## Features

- Local MP3 Playback
- Library Search and Filtering
- Playlist Creation, Renaming, and Deletion
- Automatic Artist-Tag Playlists with Manual Drag-and-Drop Fallback
- Optional Artist-Tag Display in the MP3 List
- Drag-and-Drop Playlist Management
- Shift-Click Range Selection and Multi-Song Actions
- Loop and Weighted Shuffle Playback
- Play Count, Listening Time, Duration, and Last-Played Tracking
- Library Sorting by Name, Play Time, Plays, Duration, or Last Played
- YouTube Search and Preview
- YouTube Audio Downloads with Pre-Save Renaming and Live Progress
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
- Non-Speaker Safety Mode (macOS)
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

The player stores song metadata, playlists, settings, and analysis in a single
`.mp3cache.json` file inside the selected music folder.

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
renamed, or removed. Because its contents come from filenames, individual songs
cannot be dragged into or removed from a tagged playlist. Clear the playlist's
tag to turn it back into a manual playlist that accepts drag-and-drop songs.

Playlist names do not display their assigned tags in the sidebar. The
**Show Tags** control only changes how MP3 names appear in the song list; it
does not rename files or affect automatic playlist matching.

`ffmpeg` and `ffprobe` must be available on `PATH` for audio conversion and
analysis features. On macOS, install them with `brew install ffmpeg`.

## Development

Run the test suite with:

```sh
python3 -m unittest test_play.py
```
