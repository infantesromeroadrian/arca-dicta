"""Install the local HUD without replacing hyprwhspr's daemon lifecycle."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from dicta_config import upstream_library

UPSTREAM = upstream_library()
LOCAL = Path(__file__).resolve().parent
HUD_RUNTIME = LOCAL / "hud_runtime"
HUD_ENV = "HYPRWHSPR_LOCAL_HUD"
SUPPORTED_FILES = {
    "mic_osd/main.py": "0c013a66939bc69b92e63d80d474aa27be364c203c1778fc2cf42d3d1bb724c4",
    "mic_osd/window.py": "a098a5362e98f03cbfb48ffe0b0c17b397e6d3b6fc33bf02e48c6dcf76d103aa",
    "mic_osd/theme.py": "ce77f0f69a4199bafdaa7a11c0bf08c7fe88633632b652e21be3a410b3b82604",
    "mic_osd/runner.py": "2c6017384db7822809da6dbd70c16d0105227b73d320016f6b54389fac065b75",
    "mic_osd/audio.py": "d60340ea45eca0c1d160254bd405c4fe419cca1585f8d90233895c2975e7680e",
    "mic_osd/transcript_preview.py": "8df73781005edb8f7c160cef3f611c2a504f5dfab682cadf727f82b9c4aa9c4f",
    "mic_osd/__init__.py": "f4d2da9da1f75d4b2255debe90344d895485317a12e1f81bfdcab56742a99439",
    "mic_osd/visualizations/__init__.py": "0ac11dad74fd337ce2f4ee50e79b963c35a5ed1d149e70d24f30f2f2a46d946a",
    "mic_osd/visualizations/waveform.py": "94b1749335ef7c388b53f60db97a7f9bdb1083af6996adef99f381e579ab676f",
    "mic_osd/visualizations/base.py": "76d5879dfc760520e19b780eeb52f74c2cf280bc31beef736381cd8124473375",
    "mic_osd/visualizations/pill.py": "7db28d4ed7e416ce32a750c513a6b1e0a48bb0d962306aba5b36320fb8d12071",
    "mic_osd/visualizations/vu_meter.py": "82afa22e0c541a74c45a415c72875512a99d5adbd74c13f11d2422bff7ec63d3",
    "src/paths.py": "bdd440615fabb7250a046308300721fcbf017369163107ddfd4798dbce039b84",
    "src/visualizer_runtime.py": "02d4d574ad7319902633590c5cd01e684b9b65ab7f4adf11b1b463fd6b3a6f0c",
}
LOCAL_FILES = (
    "mic_osd/__init__.py",
    "mic_osd/main.py",
    "mic_osd/hud.py",
)


def hud_supported(root: Path = UPSTREAM, runtime: Path = HUD_RUNTIME) -> bool:
    """A private upstream contract must be reviewed again after any drift."""
    try:
        if any(
            hashlib.sha256((root / name).read_bytes()).hexdigest() != expected
            for name, expected in SUPPORTED_FILES.items()
        ):
            return False
        for name in LOCAL_FILES:
            path = runtime / name
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except (OSError, SyntaxError, UnicodeError):
        return False
    return True


def _log(message: str) -> None:
    try:
        from service_log import log

        log(message)
    except ImportError:
        pass


def make_hud_runner(base: type) -> type:
    """Keep the original runner's PID, feed, IPC and teardown implementation."""

    class HudRunner(base):
        _arca_hud = True

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._native_osd_dir = self._mic_osd_dir
            if self._style == "waveform":
                self._mic_osd_dir = HUD_RUNTIME / "mic_osd"

        def _ensure_daemon(self) -> bool:
            local = self._mic_osd_dir != self._native_osd_dir
            if local and not hud_supported():
                self._mic_osd_dir = self._native_osd_dir
                os.environ.pop(HUD_ENV, None)
                local = False
                _log("[HUD] disabled reason=contract-changed")
            if not local:
                os.environ.pop(HUD_ENV, None)
            ready = super()._ensure_daemon()
            if ready or not local:
                return ready
            # One contained fallback; failed decoration must not stop dictation.
            self._mic_osd_dir = self._native_osd_dir
            os.environ.pop(HUD_ENV, None)
            _log("[HUD] disabled reason=renderer-startup")
            return super()._ensure_daemon()

    return HudRunner


def install_hud() -> bool:
    """Called once before upstream.main(); failure preserves the native UI."""
    os.environ.pop(HUD_ENV, None)
    if not hud_supported():
        _log("[HUD] disabled reason=contract-or-files")
        return False
    try:
        import mic_osd

        if not getattr(mic_osd.MicOSDRunner, "_arca_hud", False):
            mic_osd.MicOSDRunner = make_hud_runner(mic_osd.MicOSDRunner)
        os.environ[HUD_ENV] = "1"
        return True
    except Exception:
        # This is the integration boundary; never expose exception payloads.
        _log("[HUD] disabled reason=initialization")
        return False
