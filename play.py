"""Public API and command-line entry point for antarctiteex."""

import sys
from pathlib import Path

# An older editable install may know where ``play.py`` lives without having the
# sibling modules added by a later source update in its generated import map.
# Keep the source directory importable so the launcher can repair itself after
# a pull/refactor instead of failing before it can start.
_SOURCE_DIR = str(Path(__file__).resolve().parent)
if _SOURCE_DIR not in sys.path:
    sys.path.insert(0, _SOURCE_DIR)

from player_common import *
from player_engine import Player
from player_ui import *


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mp3-player",
        description="Play and manage a folder of MP3 files in the terminal.",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=os.getcwd(),
        help="MP3 library folder (default: current directory)",
    )
    args = parser.parse_args(argv)
    folder = os.path.abspath(os.path.expanduser(args.folder))

    if not TEXTUAL_AVAILABLE:
        raise SystemExit("Textual is required. Install it with: python3 -m pip install textual")
    app = MusicApp(folder)
    try:
        app.run()
    finally:
        # Textual normally invokes on_unmount, but this also covers Ctrl+C,
        # startup failures, and exceptions during event handling.
        if app.player:
            app.player._shutdown_audio()


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
