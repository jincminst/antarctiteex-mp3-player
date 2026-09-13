#!/usr/bin/env python3
"""Local streamlined launcher for Jin's MP3 player."""

import argparse
import os

import play


if play.TEXTUAL_AVAILABLE:
    class PersonalMusicApp(play.MusicApp):
        """Use the main player without exposing the Show Tags control."""

        CSS = play.MusicApp.CSS + """
        #tags-row { display: none; }
        """

        def refresh_ui(self, *args, **kwargs):
            # Personal mode always renders the full filename. Restore the
            # shared preference afterward so this launcher does not overwrite
            # the public app's Show Tags choice when another setting is saved.
            if not self.player:
                return super().refresh_ui(*args, **kwargs)
            saved_show_tags = self.player.show_tags
            self.player.show_tags = True
            try:
                return super().refresh_ui(*args, **kwargs)
            finally:
                self.player.show_tags = saved_show_tags
else:
    PersonalMusicApp = None


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="antarctiteex-personal",
        description="Launch Jin's streamlined terminal MP3 player.",
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=os.getcwd(),
        help="MP3 library folder (default: current directory)",
    )
    args = parser.parse_args(argv)
    folder = os.path.abspath(os.path.expanduser(args.folder))

    if PersonalMusicApp is None:
        raise SystemExit(
            "Textual is required. Install it with: python3 -m pip install textual"
        )
    PersonalMusicApp(folder).run()


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            play.pygame.mixer.quit()
        except Exception:
            pass
