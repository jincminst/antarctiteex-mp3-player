#!/usr/bin/env python3
"""Silently monitor Apple Music Hits for "drivers license" on macOS."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass


FIELD_SEPARATOR = "\x1f"
STATION_NAME = "Apple Music Hits"
STATION_URL = "music://music.apple.com/us/radio/apple-music-hits/ra.1498155548"
TARGET_SONG = "drivers license"
TARGET_ARTIST = "Olivia Rodrigo"

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

MUSIC_VOLUME_SCRIPT = r'''
tell application "Music"
    return sound volume as string
end tell
'''

MUTE_MUSIC_SCRIPT = r'''
tell application "Music"
    set sound volume to 0
end tell
'''

STOP_AND_RESTORE_SCRIPT = r'''
on run argv
    tell application "Music"
        pause
        set sound volume to (item 1 of argv as integer)
    end tell
end run
'''

NOTIFICATION_SCRIPT = r'''
on run argv
    display notification (item 1 of argv) with title (item 2 of argv)
end run
'''


class MusicMonitorError(RuntimeError):
    """Raised when macOS cannot control or query Music."""


@dataclass(frozen=True)
class Playback:
    state: str
    title: str = ""
    artist: str = ""
    track_id: str = ""
    position: float | None = None


def normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def matches(playback: Playback) -> bool:
    return (
        normalized(playback.title) == normalized(TARGET_SONG)
        and normalized(playback.artist) == normalized(TARGET_ARTIST)
    )


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


def run_osascript(osascript: str, script: str, *arguments: str) -> str:
    command = [osascript, "-e", script]
    if arguments:
        command.extend(["--", *arguments])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or "Apple Music could not be controlled"
        raise MusicMonitorError(detail)
    return result.stdout.strip()


def read_playback(osascript: str) -> Playback:
    return parse_playback(run_osascript(osascript, MUSIC_STATUS_SCRIPT))


def get_music_volume(osascript: str) -> int:
    output = run_osascript(osascript, MUSIC_VOLUME_SCRIPT)
    try:
        return max(0, min(100, int(output)))
    except ValueError as exc:
        raise MusicMonitorError(f"Music returned an invalid volume: {output!r}") from exc


def start_silent_hits(osascript: str, open_command: str) -> int:
    """Mute Music, open Apple Music Hits, and return the prior volume."""
    old_volume = get_music_volume(osascript)
    run_osascript(osascript, MUTE_MUSIC_SCRIPT)
    result = subprocess.run(
        [open_command, STATION_URL], capture_output=True, text=True, check=False
    )
    if result.returncode:
        run_osascript(osascript, STOP_AND_RESTORE_SCRIPT, str(old_volume))
        detail = result.stderr.strip() or f"{STATION_NAME} could not be opened"
        raise MusicMonitorError(detail)
    return old_volume


def stop_and_restore(osascript: str, old_volume: int) -> None:
    run_osascript(osascript, STOP_AND_RESTORE_SCRIPT, str(old_volume))


def send_notification(osascript: str, playback: Playback) -> None:
    run_osascript(
        osascript,
        NOTIFICATION_SCRIPT,
        f"{playback.title} — {playback.artist}",
        f"Now on {STATION_NAME}",
    )


def monitor(interval: float, osascript: str, open_command: str) -> None:
    old_volume = start_silent_hits(osascript, open_command)
    notified_track = ""
    last_position: float | None = None

    print(
        f'Silently monitoring {STATION_NAME} for "{TARGET_SONG}" by '
        f'"{TARGET_ARTIST}". Press Ctrl-C to stop.'
    )
    try:
        while True:
            playback = read_playback(osascript)
            is_target = matches(playback)

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
    finally:
        stop_and_restore(osascript, old_volume)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            f'Silently monitor {STATION_NAME} for "{TARGET_SONG}" by '
            f'"{TARGET_ARTIST}".'
        )
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=150.0,
        help="seconds between checks (default: 150, or 2.5 minutes)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval < 0.2:
        print("--interval must be at least 0.2 seconds.", file=sys.stderr)
        return 2
    if sys.platform != "darwin":
        print("This monitor requires macOS and the Music app.", file=sys.stderr)
        return 2
    osascript = shutil.which("osascript")
    open_command = shutil.which("open")
    if not osascript or not open_command:
        print("Required macOS commands were not found.", file=sys.stderr)
        return 2

    try:
        monitor(args.interval, osascript, open_command)
    except KeyboardInterrupt:
        print("\nStopped Apple Music Hits and restored the previous volume.")
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
