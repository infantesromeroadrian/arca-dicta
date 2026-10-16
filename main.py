#!/usr/bin/env python3
"""Start the installed app with a reversible, version-checked OSD preview layer."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import time
from pathlib import Path

from dicta_config import (
    ConfigurationError, RuntimeConfig, load_runtime_config,
    upstream_binary, upstream_library,
)

sys.dont_write_bytecode = True

LOCAL = Path(__file__).resolve().parent
# These files define the private capture/playback/lifecycle contract of 1.49.0-1.
# An update stops this daemon until its new contract has been checked.
SUPPORTED_FILES = {
    "main.py": "e140723bbdf062b70c1ca20320bdca41bbbf6a698b13779b1ac1b0cf60a4fa00",
    "src/text_injector.py": "41baf6fdc210f76e415d39e0f4b26896157d637684d596ef3bd4c7a1ebdce1e0",
    "src/app/recovery.py": "65242cc516ee2000033c55463cd8fead8e481ecc111ac1945fe9844bc0236880",
    "src/app/control.py": "8833f819b6611c1f6341c08121def098929c51cba034edee1c703429bf52c761",
    "src/app/shortcuts.py": "676bbf1856f856f12dfec1c8303de34c22c022436f2dbd6299e7b1e6e9dbb2c1",
    "src/audio_capture.py": "8e55303aa451e4f7cfbfb179415ca914c5cbcb6c79745d0963541bb6775fb2a6",
    "src/app/recording.py": "32950327dbcf322c347d5dd9376afc2f5bb3fe23ea5a03f5a7cfeac383127cf2",
    "src/app/feedback.py": "57eef8db7a815c0952aec0a4fca792ec17c43bfe5e7a716e8ad06fe1970aa8c8",
    "mic_osd/runner.py": "2c6017384db7822809da6dbd70c16d0105227b73d320016f6b54389fac065b75",
    "src/audio_ducker.py": "284bd8ca8ec7745eedb8f07b5e459595e903ce552d808b8d7bfcca05443f8d2f",
    "src/playback_suppressor.py": "de49d99f25587b57c23abfd63040faa9ea7d391a6a0e02ac8e8aa6b504a3bc1d",
    "src/media_pauser.py": "35e90b896880efc0a243512528eebefb8fba027ae8ea7db847fc3a38a121a465",
}


def upstream_supported(root: Path | None = None) -> bool:
    if root is None:
        root = upstream_library()
    try:
        return all(
            hashlib.sha256((root / filename).read_bytes()).hexdigest() == expected
            for filename, expected in SUPPORTED_FILES.items()
        )
    except OSError:
        return False


def make_preview_app(base, controller_factory):
    class PreviewApp(base):
        def __init__(self) -> None:
            self._local_preview = None
            super().__init__()
            if (
                self.config.get_setting("transcription_backend") == "rest-api"
                and self.config.get_setting("recording_mode", "toggle")
                in {"auto", "toggle", "push_to_talk"}
                and self._mic_osd_runner is not None
            ):
                try:
                    self._local_preview = controller_factory(self)
                except Exception:
                    from service_log import log

                    log(
                        "[LIVE-PREVIEW] disabled reason=initialization-failure; final transcription unchanged"
                    )

        def _start_recording(self, language_override=None):
            previous = self._recording_session
            result = super()._start_recording(language_override=language_override)
            with self._recording_lock:
                token = self._recording_session
                active = self.is_recording and token is not previous
                started_at = self._ptt_press_time
            if self._local_preview and active:
                language = (
                    language_override
                    if language_override is not None
                    else self.config.get_setting("language")
                )
                if language in ("auto", ""):
                    language = None
                self._local_preview.begin(
                    token, language, started_at or time.monotonic()
                )
            return result

        def _stop_recording(self, expected_session=None):
            if self._local_preview and (
                expected_session is None or expected_session is self._recording_session
            ):
                self._local_preview.invalidate(self._recording_session)
            return super()._stop_recording(expected_session=expected_session)

        def _cancel_recording(self):
            if self._local_preview:
                self._local_preview.invalidate(self._recording_session)
            return super()._cancel_recording()

        def _clear_mic_osd_preview_text(self):
            if self._local_preview:
                self._local_preview.invalidate()
            return super()._clear_mic_osd_preview_text()

        def _hide_mic_osd(self):
            if self._local_preview:
                self._local_preview.invalidate()
            return super()._hide_mic_osd()

        def _cleanup(self):
            if self._local_preview:
                self._local_preview.close()
            return super()._cleanup()

    return PreviewApp


def build_controller(app, config: RuntimeConfig):
    from preview import LocalDecoder, PreviewController, capture_tail
    from service_log import log

    return PreviewController(
        decoder=LocalDecoder(config.worker_python, config.model_dir, LOCAL / "worker.py"),
        snapshot=lambda token: capture_tail(app, token),
        is_active=lambda token: app.is_recording and app._recording_session is token,
        publish=app._mic_osd_runner.set_preview_text,
        clear=app._mic_osd_runner.clear_preview_text,
        status=app._mic_osd_runner.set_state,
        log=log,
    )


def main() -> None:
    if len(sys.argv) > 1:
        binary = str(upstream_binary())
        os.execv(binary, [binary, *sys.argv[1:]])
    try:
        supported = upstream_supported()
        if not supported:
            raise SystemExit("[REVIEW] Upstream contract changed; strict dictation stopped")
        config = load_runtime_config()
    except ConfigurationError as exc:
        raise SystemExit(f"[CONFIG] {exc}; strict dictation stopped") from None
    os.environ["HYPRWHSPR_ROOT"] = str(config.upstream_lib.parent)
    spec = importlib.util.spec_from_file_location(
        "hyprwhspr_upstream", config.upstream_lib / "main.py"
    )
    upstream = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = upstream
    spec.loader.exec_module(upstream)
    if supported:
        from playback import install_playback

        install_playback(upstream)
        try:
            from hud_bridge import install_hud

            install_hud()
        except Exception:
            upstream.log("[LOCAL-HUD] disabled reason=initialization-failure")
        upstream.hyprwhsprApp = make_preview_app(
            upstream.hyprwhsprApp, lambda app: build_controller(app, config)
        )
    from review_gate import install_review

    install_review(upstream)
    upstream.main()


if __name__ == "__main__":
    main()
