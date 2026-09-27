#!/usr/bin/env python3
"""Notify when Apple Music starts playing a chosen song on macOS."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass


FIELD_SEPARATOR = "\x1f"
MUSIC_STATUS_SCRIPT = r'''
tell application "Music"
    set playbackState to (player state as string)
    try
        set trackName to (name of current track as string)
        set trackArtist to (artist of current track as string)
    on error
        return playbackState
    end try
    try
        set trackID to (persistent ID of current track as string)
    on error
        set trackID to trackName & " - " & trackArtist
    end try
    try
        set trackPosition to (player position as string)
    on error
        set trackPosition to ""
    end try
    return playbackState & (character id 31) & trackName & (character id 31) & trackArtist & (character id 31) & trackID & (character id 31) & trackPosition
end tell
'''

NOTIFICATION_SCRIPT = r'''
on run argv
    display notification (item 1 of argv) with title (item 2 of argv)
end run
'''


class MusicMonitorError(RuntimeError):
    """Raised when macOS cannot provide Music playback information."""


@dataclass(frozen=True)
class Playback:
    state: str
    title: str = ""
    artist: str = ""
    track_id: str = ""
    position: float | None = None


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def matches(playback: Playback, song: str, artist: str = "") -> bool:
    if normalized(playback.title) != normalized(song):
        return False
    return not artist or normalized(playback.artist) == normalized(artist)


def parse_playback(output: str) -> Playback:
    fields = output.rstrip("\r\n").split(FIELD_SEPARATOR)
    state = fields[0].strip().lower() if fields else ""
    if len(fields) < 5:
        return Playback(state=state)
    try:
        position = float(fields[4])
    except (TypeError, ValueError):
        position = None
    return Playback(
        state=state,
        title=fields[1],
        artist=fields[2],
        track_id=fields[3],
        position=position,
    )


def read_playback(osascript: str) -> Playback:
    running = subprocess.run(
        ["pgrep", "-x", "Music"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if running.returncode:
        return Playback("not_running")
    result = subprocess.run(
        [osascript, "-e", MUSIC_STATUS_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or "Apple Music could not be queried"
        raise MusicMonitorError(detail)
    return parse_playback(result.stdout)


def send_notification(osascript: str, playback: Playback) -> None:
    message = playback.title
    if playback.artist:
        message += f" — {playback.artist}"
    result = subprocess.run(
        [
            osascript,
            "-e",
            NOTIFICATION_SCRIPT,
            "--",
            message,
            "Apple Music match",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or "Notification could not be sent"
        raise MusicMonitorError(detail)


def monitor(song: str, artist: str, interval: float, osascript: str) -> None:
    notified_track = ""
    last_position: float | None = None

    print(f'Watching Apple Music for "{song}"', end="")
    if artist:
        print(f' by "{artist}"', end="")
    print(". Press Ctrl-C to stop.")

    while True:
        playback = read_playback(osascript)
        is_target = matches(playback, song, artist)

        if not is_target:
            notified_track = ""
            last_position = None
        elif playback.state == "playing":
            track_key = playback.track_id or f"{playback.title}\0{playback.artist}"
            restarted = (
                notified_track == track_key
                and playback.position is not None
                and last_position is not None
                and playback.position + max(1.0, interval) < last_position
            )
            if notified_track != track_key or restarted:
                send_notification(osascript, playback)
                print(f"Match: {playback.title} — {playback.artist}")
                notified_track = track_key
            last_position = playback.position

        time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Notify when Apple Music starts playing a chosen song."
    )
    parser.add_argument("song", nargs="?", help="exact song title to watch for")
    parser.add_argument("--artist", default="", help="optional exact artist name")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="seconds between checks (default: 1)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    song = (args.song or input("Song title: ")).strip()
    if not song:
        print("A song title is required.", file=sys.stderr)
        return 2
    if args.interval < 0.2:
        print("--interval must be at least 0.2 seconds.", file=sys.stderr)
        return 2
    if sys.platform != "darwin":
        print("This monitor requires macOS and the Music app.", file=sys.stderr)
        return 2
    osascript = shutil.which("osascript")
    if not osascript:
        print("osascript was not found.", file=sys.stderr)
        return 2

    try:
        monitor(song, args.artist.strip(), args.interval, osascript)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except MusicMonitorError as exc:
        print(f"Apple Music monitor failed: {exc}", file=sys.stderr)
        print(
            "If macOS asks, allow your terminal to control Music and send notifications.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
