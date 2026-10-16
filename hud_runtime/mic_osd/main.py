"""Delegate signals, state polling and shutdown to the installed mic-osd."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

from . import UPSTREAM


def load_native():
    name = "mic_osd._native_main"
    spec = importlib.util.spec_from_file_location(
        name, UPSTREAM / "mic_osd/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def use_capture_feed_only(native) -> None:
    """Even a missing/stale feed must never open another audio device."""
    def existing_capture(*args, **kwargs):
        return native.FeedLevelSource(native.MIC_OSD_LEVEL_FEED_FILE)

    native.AudioMonitor = existing_capture


def native_visualizations_with_review_state(original: dict) -> dict:
    """The native drawing fallback must not mislabel reviewing as recording."""
    waveform = original["waveform"]

    class NativeWaveform(waveform):
        def set_state(self, state: str) -> None:
            if state in {"listening", "decoding", "preview_unavailable"}:
                state = "recording"
            if state in {"review_failed", "review_blocked"}:
                state = "error"
            super().set_state("processing" if state == "reviewing" else state)

    return {**original, "waveform": NativeWaveform}


def refresh_state_on_show(base: type) -> type:
    class HudMicOSD(base):
        def _show(self):
            if not self.visible:
                # The window forgets its display cache on hide. Re-read even
                # when two sessions both start with preview_unavailable.
                self._last_visualizer_state = None
            return super()._show()

    return HudMicOSD


def main() -> int:
    local = Path(__file__).resolve().parents[2]
    if str(local) not in sys.path:
        sys.path.append(str(local))
    native = load_native()
    use_capture_feed_only(native)
    original_window = native.OSDWindow
    original_app = native.MicOSD
    original_visualizations = native.VISUALIZATIONS
    original_args = sys.argv[:]
    supported = False
    try:
        from hud_bridge import hud_supported

        # The level feed remains native. Its absence is silence, never a
        # second microphone stream, including when the visual UI falls back.
        supported = hud_supported()
        if not supported:
            os.environ.pop("HYPRWHSPR_LOCAL_HUD", None)
            return native.main()
        from .hud import HudVisualization, HudWindow

        native.OSDWindow = HudWindow
        native.MicOSD = refresh_state_on_show(original_app)
        native.VISUALIZATIONS = {
            **native.VISUALIZATIONS,
            "waveform": HudVisualization,
        }
        sys.argv = [*original_args, "--width", "560", "--height", "124"]
        return native.main()
    except Exception:
        # A renderer/import failure must not take the service's dictation
        # path down. The original lifecycle remains the sole owner of IPC.
        if native._app is not None:
            try:
                native._app._cleanup()
            except Exception:
                pass
        os.environ.pop("HYPRWHSPR_LOCAL_HUD", None)
        native.OSDWindow = original_window
        native.MicOSD = original_app
        native.VISUALIZATIONS = (
            native_visualizations_with_review_state(original_visualizations)
            if supported else original_visualizations
        )
        sys.argv = original_args
        return native.main()
