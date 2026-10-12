"""macOS media controls, screen state, and audio-output safety."""

from player_common import *


class SystemMixin:
    """macOS media controls, screen state, and audio-output safety."""

    def _start_screen_lock_watcher(self):
        watcher = None
        try:
            import Quartz

            def _watch_quartz():
                while self.running:
                    time.sleep(SCREEN_LOCK_POLL_S)
                    try:
                        d = Quartz.CGSessionCopyCurrentDictionary()
                        locked = bool(d and d.get("CGSSessionScreenIsLocked", False))
                    except Exception:
                        locked = False
                    self._handle_lock_state(locked)

            watcher = _watch_quartz
        except ImportError:
            pass
        if watcher is None:
            try:
                import ctypes
                import ctypes.util

                path = ctypes.util.find_library("ApplicationServices")
                if path:
                    appserv = ctypes.cdll.LoadLibrary(path)
                    appserv.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
                    cf = ctypes.cdll.LoadLibrary(
                        ctypes.util.find_library("CoreFoundation")
                    )
                    cf.CFRelease.argtypes = [ctypes.c_void_p]
                    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
                    cf.CFStringCreateWithCString.argtypes = [
                        ctypes.c_void_p,
                        ctypes.c_char_p,
                        ctypes.c_uint32,
                    ]
                    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
                    cf.CFDictionaryGetValue.argtypes = [
                        ctypes.c_void_p,
                        ctypes.c_void_p,
                    ]
                    cf.CFBooleanGetValue.restype = ctypes.c_bool
                    cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
                    cf.CFGetTypeID.restype = ctypes.c_ulong
                    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
                    cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
                    cf.CFNumberGetValue.restype = ctypes.c_bool
                    cf.CFNumberGetValue.argtypes = [
                        ctypes.c_void_p,
                        ctypes.c_int,
                        ctypes.c_void_p,
                    ]
                    cf.CFNumberGetTypeID.restype = ctypes.c_ulong
                    kCFStringEncodingUTF8 = 0x08000100
                    key_cfstr = cf.CFStringCreateWithCString(
                        None, b"CGSSessionScreenIsLocked", kCFStringEncodingUTF8
                    )

                    def _watch_ctypes():
                        while self.running:
                            time.sleep(SCREEN_LOCK_POLL_S)
                            locked = False
                            try:
                                d = appserv.CGSessionCopyCurrentDictionary()
                                if d:
                                    val = cf.CFDictionaryGetValue(d, key_cfstr)
                                    if val:
                                        type_id = cf.CFGetTypeID(val)
                                        if type_id == cf.CFBooleanGetTypeID():
                                            locked = cf.CFBooleanGetValue(val)
                                        elif type_id == cf.CFNumberGetTypeID():
                                            out = ctypes.c_int(0)
                                            cf.CFNumberGetValue(
                                                val, 9, ctypes.byref(out)
                                            )
                                            locked = out.value != 0
                                    cf.CFRelease(d)
                            except Exception:
                                locked = False
                            self._handle_lock_state(locked)

                    watcher = _watch_ctypes
            except Exception:
                pass
        if watcher is None:
            try:

                def _watch_ioreg():
                    while self.running:
                        time.sleep(SCREEN_LOCK_POLL_S)
                        try:
                            out = subprocess.check_output(
                                ["ioreg", "-n", "Root", "-d1", "-w0"],
                                stderr=subprocess.DEVNULL,
                                text=True,
                                timeout=5,
                            )
                            locked = "CGSSessionScreenIsLocked" in out
                        except Exception:
                            locked = False
                        self._handle_lock_state(locked)

                watcher = _watch_ioreg
            except Exception:
                pass
        if watcher is not None:
            threading.Thread(target=watcher, daemon=True).start()

    def _handle_lock_state(self, locked):
        if locked and not self._screen_locked:
            self._screen_locked = True
            if self.current not in ("None", "Loading...") and not self.paused:
                self._paused_by_lock = True
                self._pending_media_toggle = True
        elif not locked and self._screen_locked:
            self._screen_locked = False
            if self._paused_by_lock and self.paused:
                self._paused_by_lock = False
                self._pending_media_toggle = True
            else:
                self._paused_by_lock = False

    def _wake_ui(self):
        if callable(self._ui_wakeup):
            try:
                self._ui_wakeup()
            except RuntimeError:
                # A UI-button safety check is already on Textual's app thread;
                # call_from_thread is only valid for the background watchers.
                pass
            return
        try:
            curses.ungetch(0)
        except Exception:
            pass

    def _start_media_key_listener(self):
        # On macOS, headset/earphone center buttons arrive as native
        # systemDefined consumer-key events. Run both available paths because
        # some headset drivers expose the control only to one of them.
        if sys.platform == "darwin":
            self._start_macos_media_key_listener()
        try:
            import contextlib
            import io

            with contextlib.redirect_stderr(io.StringIO()):
                from pynput import keyboard

                def on_press(key):
                    try:
                        if key == keyboard.Key.media_play_pause:
                            self._queue_media_button_toggle()
                        elif key == getattr(keyboard.Key, "media_next", None):
                            self._queue_media_button_command("NEXT")
                        elif key == getattr(keyboard.Key, "media_previous", None):
                            self._queue_media_button_command("PREVIOUS")
                    except Exception:
                        pass

                listener = keyboard.Listener(on_press=on_press)
                listener.daemon = True
                listener.start()
            return
        except Exception:
            pass

    def _queue_media_button_toggle(self):
        """Queue one toggle while deduplicating the same hardware press."""
        return self._queue_media_button_command("TOGGLE")

    def _queue_media_button_command(self, command):
        """Queue a remote transport command, deduplicating dual macOS paths."""
        command = str(command or "").strip().upper()
        if command not in {"TOGGLE", "PLAY", "PAUSE", "NEXT", "PREVIOUS"}:
            return False
        now = time.monotonic()
        command_group = (
            "PLAYBACK" if command in {"TOGGLE", "PLAY", "PAUSE"} else command
        )
        with self._media_button_lock:
            if (
                command_group == getattr(self, "_last_media_button_command", "")
                and now - self._last_media_button_at < 0.35
            ):
                return False
            self._last_media_button_at = now
            self._last_media_button_command = command_group
            if not hasattr(self, "_pending_media_commands"):
                self._pending_media_commands = deque()
            self._pending_media_commands.append(command)
        self._wake_ui()
        return True

    @staticmethod
    def _macos_media_key_swift_source():
        """Return the native helper that owns headset and system media keys."""
        return r'''import Cocoa
import MediaPlayer

let commandCenter = MPRemoteCommandCenter.shared()
let infoCenter = MPNowPlayingInfoCenter.default()
let commands: [MPRemoteCommand] = [
    commandCenter.togglePlayPauseCommand,
    commandCenter.playCommand,
    commandCenter.pauseCommand,
    commandCenter.nextTrackCommand,
    commandCenter.previousTrackCommand,
]

func emit(_ value: String) {
    print(value)
    fflush(stdout)
}

func enableCommands(_ enabled: Bool) {
    for command in commands { command.isEnabled = enabled }
}

commandCenter.togglePlayPauseCommand.addTarget { _ in
    emit("TOGGLE")
    return .success
}
commandCenter.playCommand.addTarget { _ in
    emit("PLAY")
    return .success
}
commandCenter.pauseCommand.addTarget { _ in
    emit("PAUSE")
    return .success
}
commandCenter.nextTrackCommand.addTarget { _ in
    emit("NEXT")
    return .success
}
commandCenter.previousTrackCommand.addTarget { _ in
    emit("PREVIOUS")
    return .success
}

NSEvent.addGlobalMonitorForEvents(matching: .systemDefined) { event in
    if event.subtype.rawValue == 8 {
        let keyCode = (event.data1 & 0xFFFF0000) >> 16
        let keyFlags = event.data1 & 0x0000FFFF
        let keyDown = ((keyFlags & 0xFF00) >> 8) == 0xA
        if keyDown {
            if keyCode == 16 { emit("TOGGLE") }
            if keyCode == 17 { emit("NEXT") }
            if keyCode == 18 { emit("PREVIOUS") }
        }
    }
}

enableCommands(false)
infoCenter.playbackState = .stopped
emit("READY")

DispatchQueue.global(qos: .userInitiated).async {
    while let line = readLine() {
        let pieces = line.split(separator: "\t", maxSplits: 1).map(String.init)
        let state = pieces.first ?? "STOPPED"
        let title = pieces.count > 1 ? pieces[1] : "antarctiteex MP3 Player"
        DispatchQueue.main.async {
            switch state {
            case "PLAYING":
                enableCommands(true)
                infoCenter.nowPlayingInfo = [MPMediaItemPropertyTitle: title]
                infoCenter.playbackState = .playing
            case "PAUSED":
                enableCommands(true)
                infoCenter.nowPlayingInfo = [MPMediaItemPropertyTitle: title]
                infoCenter.playbackState = .paused
            default:
                enableCommands(false)
                infoCenter.nowPlayingInfo = nil
                infoCenter.playbackState = .stopped
            }
        }
    }
}
RunLoop.main.run()
'''

    def _sync_macos_media_state(self):
        """Tell macOS when this player should own remote headset commands."""
        proc = getattr(self, "_media_key_proc", None)
        if sys.platform != "darwin" or proc is None or proc.stdin is None:
            return False
        current = str(self.current or "")
        active = (
            current not in ("", "None", "Loading...")
            and not self.apple_radio_enabled
        )
        state = "PAUSED" if active and self.paused else "PLAYING" if active else "STOPPED"
        title = re.sub(r"[\t\r\n]+", " ", current).strip()
        try:
            with self._media_button_lock:
                proc.stdin.write(f"{state}\t{title}\n")
                proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def _start_macos_media_key_listener(self):
        try:
            helper = tempfile.NamedTemporaryFile(
                mode="w", suffix=".swift", prefix="antarctiteex-media-",
                delete=False, encoding="utf-8",
            )
            helper.write(self._macos_media_key_swift_source())
            helper.close()
            self._media_key_helper_path = helper.name
            proc = subprocess.Popen(
                ["swift", helper.name],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            self._media_key_proc = proc

            def reader():
                while self.running:
                    try:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        command = line.strip().upper()
                        if command == "READY":
                            self._sync_macos_media_state()
                        elif command in {
                            "TOGGLE", "PLAY", "PAUSE", "NEXT", "PREVIOUS"
                        }:
                            self._queue_media_button_command(command)
                    except Exception:
                        break
                try:
                    os.unlink(helper.name)
                except OSError:
                    pass
                if self._media_key_helper_path == helper.name:
                    self._media_key_helper_path = ""

            threading.Thread(target=reader, daemon=True).start()
            return True
        except Exception:
            helper_path = getattr(self, "_media_key_helper_path", "")
            if helper_path:
                try:
                    os.unlink(helper_path)
                except OSError:
                    pass
                self._media_key_helper_path = ""
            return False

    @staticmethod
    def _is_builtin_speaker_name(name):
        normalized = str(name or "").strip().lower()
        return normalized in {"built-in output", "mac speakers"} or (
            "speaker" in normalized
            and any(word in normalized for word in ("built-in", "macbook", "imac", "mac "))
        )

    @staticmethod
    def _is_find_my_alert_log(line):
        text = str(line or "").lower()
        source = any(token in text for token in (
            "findmy", "find my", "fmfd", "findmydeviced",
        ))
        alert = any(token in text for token in (
            "play sound", "playsound", "play_sound", "sound alert", "alert sound",
        ))
        return source and alert

    def _halt_for_speaker_safety(self, force_enable=False):
        """Immediately silence playback; safe to call from watcher threads."""
        with self._safety_lock:
            if force_enable:
                self.non_speaker_mode = True
                self._output_safety_wakeup.set()
            if self.current in ("None", "Loading...") or self.paused:
                self.dirty = True
                self._wake_ui()
                return
            self._pause_start = time.monotonic()
            try:
                pygame.mixer.music.pause()
            except Exception:
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
            self.paused = True
            self._paused_by_speaker_safety = True
            self.status_msg = "Paused: built-in speaker safety"
            self.dirty = True
            self._needs_redraw_hdr = True
            self._needs_redraw_bar = True
        self._sync_macos_media_state()
        self._wake_ui()

    def _resume_from_speaker_safety(self):
        """Resume only audio that speaker safety itself paused."""
        with self._safety_lock:
            if (
                not self._paused_by_speaker_safety
                or not self.non_speaker_mode
                or not self.paused
                or self.current in ("None", "Loading...")
                or self._is_builtin_speaker_name(self._current_output_device)
            ):
                return False
            self._paused_by_speaker_safety = False
            self._pause_offset += time.monotonic() - self._pause_start
            try:
                pygame.mixer.music.unpause()
                self._apply_volume(force=True)
            except Exception:
                pass
            self.paused = False
            self.status_msg = "Resumed: earphones connected"
            self.dirty = True
            self._needs_redraw_hdr = True
            self._needs_redraw_bar = True
        self._sync_macos_media_state()
        self._wake_ui()
        return True

    def _handle_output_device(self, name):
        name = str(name or "").strip()
        self._current_output_device = name
        is_speaker = self._is_builtin_speaker_name(name)
        if (
            name
            and self.non_speaker_mode
            and is_speaker
            and not self.paused
        ):
            self._halt_for_speaker_safety()
        elif (
            name
            and self.non_speaker_mode
            and not is_speaker
            and self._paused_by_speaker_safety
        ):
            self._resume_from_speaker_safety()

    def _install_output_change_listener(self):
        """Wake the safety watcher when CoreAudio changes the default output."""
        if sys.platform != "darwin":
            return False
        try:
            coreaudio = ctypes.CDLL(
                "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
            )
            address = _AudioObjectPropertyAddress(
                int.from_bytes(b"dOut", "big"),
                int.from_bytes(b"glob", "big"), 0,
            )

            def changed(_object_id, _count, _addresses, _context):
                if self.running and not self._shutting_down and self.non_speaker_mode:
                    self._output_route_changed.set()
                    self._output_safety_wakeup.set()
                return 0

            callback = _AUDIO_PROPERTY_LISTENER(changed)
            add = coreaudio.AudioObjectAddPropertyListener
            add.argtypes = [
                ctypes.c_uint32, ctypes.POINTER(_AudioObjectPropertyAddress),
                _AUDIO_PROPERTY_LISTENER, ctypes.c_void_p,
            ]
            add.restype = ctypes.c_int32
            if add(1, ctypes.byref(address), callback, None) != 0:
                return False
            self._audio_output_listener = (coreaudio, address, callback)
            if self._shutting_down or not self.running:
                self._remove_output_change_listener()
                return False
            return True
        except Exception:
            return False

    def _remove_output_change_listener(self):
        listener = self._audio_output_listener
        self._audio_output_listener = None
        if not listener:
            return
        coreaudio, address, callback = listener
        try:
            remove = coreaudio.AudioObjectRemovePropertyListener
            remove.argtypes = [
                ctypes.c_uint32, ctypes.POINTER(_AudioObjectPropertyAddress),
                _AUDIO_PROPERTY_LISTENER, ctypes.c_void_p,
            ]
            remove.restype = ctypes.c_int32
            remove(1, ctypes.byref(address), callback, None)
        except Exception:
            pass

    def _start_output_safety_watcher(self):
        if sys.platform != "darwin":
            return
        switch_audio = shutil.which("SwitchAudioSource")
        if not switch_audio:
            return
        event_listener_ready = self._install_output_change_listener()

        def watch_output():
            while self.running and not self._shutting_down:
                # With safety disabled there is nothing to enforce. Sleeping
                # on an event avoids continuously spawning a command-line
                # helper in an otherwise idle player; the toggle wakes this
                # thread immediately when safety is enabled again.
                if not self.non_speaker_mode:
                    self._output_safety_wakeup.wait(timeout=60.0)
                    self._output_safety_wakeup.clear()
                    continue
                if self._output_route_changed.is_set():
                    self._output_route_changed.clear()
                    if self.current not in ("None", "Loading...") and not self.paused:
                        self._halt_for_speaker_safety()
                try:
                    result = subprocess.run(
                        [switch_audio, "-c", "-t", "output"],
                        capture_output=True, text=True, timeout=1,
                    )
                    if result.returncode == 0:
                        self._handle_output_device(result.stdout)
                except Exception:
                    pass
                active = (
                    self.current not in ("None", "Loading...")
                    and not self.paused
                )
                interval = (
                    OUTPUT_SAFETY_ACTIVE_POLL_S
                    if active
                    else OUTPUT_SAFETY_IDLE_POLL_S
                )
                if active and not event_listener_ready:
                    interval = OUTPUT_SAFETY_FALLBACK_POLL_S
                self._output_safety_wakeup.wait(timeout=interval)
                self._output_safety_wakeup.clear()

        threading.Thread(target=watch_output, daemon=True).start()

    def _start_find_my_alert_watcher(self):
        if sys.platform != "darwin" or not shutil.which("log"):
            return
        try:
            proc = subprocess.Popen(
                [
                    "/usr/bin/log", "stream", "--style", "ndjson", "--level", "info",
                    "--predicate",
                    'process CONTAINS[c] "findmy" OR process == "fmfd" OR senderImagePath CONTAINS[c] "findmy"',
                ],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            self._find_my_log_proc = proc

            def read_find_my_log():
                while self.running and not self._shutting_down:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    if self._is_find_my_alert_log(line):
                        self._halt_for_speaker_safety(force_enable=True)

            threading.Thread(target=read_find_my_log, daemon=True).start()
        except Exception:
            self._find_my_log_proc = None
