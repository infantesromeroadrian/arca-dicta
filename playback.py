"""Temporary sink muting for full suppression, without stream-volume writes."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SinkIdentity:
    name: str
    driver: str
    device: tuple[str | None, ...]

    @classmethod
    def from_sink(cls, sink: Any) -> SinkIdentity:
        # Indexes are recycled. Names plus device properties survive reconnects
        # without confusing a new device which happens to receive the old index.
        return cls(
            sink.name,
            sink.driver,
            tuple(sink.proplist.get(key) for key in (
                "device.serial", "device.bus_path", "device.string", "device.api",
            )),
        )


class SinkMuteGuard:
    """Own only mute transitions we applied; failed restores remain retryable."""

    def __init__(self, pulse_factory: Callable[..., Any], log: Callable[[str], None]) -> None:
        self._pulse_factory = pulse_factory
        self._log = log
        self._owned: dict[SinkIdentity, bool] = {}
        self._cycle_active = False
        self._lock = threading.Lock()

    def suppress(self) -> bool:
        with self._lock:
            if self._cycle_active:
                return True
            ok = True
            try:
                with self._pulse_factory("hyprwhspr-output-mute") as pulse:
                    for sink in pulse.sink_list():
                        if sink.mute:
                            continue
                        identity = SinkIdentity.from_sink(sink)
                        # Record before the call: a transport error may arrive
                        # after the server has already applied the change.
                        self._owned[identity] = bool(sink.mute)
                        try:
                            pulse.sink_mute(sink.index, True)
                        except Exception as exc:
                            self._log(f"[PLAYBACK-MUTE] suppress-failed error={type(exc).__name__}")
                            ok = False
                self._cycle_active = True
            except Exception as exc:
                self._log(f"[PLAYBACK-MUTE] connection-failed error={type(exc).__name__}")
                ok = False
            return ok

    def restore(self) -> bool:
        with self._lock:
            self._cycle_active = False
            if not self._owned:
                return True
            try:
                with self._pulse_factory("hyprwhspr-output-mute") as pulse:
                    sinks = {SinkIdentity.from_sink(sink): sink for sink in pulse.sink_list()}
                    for identity, original_mute in list(self._owned.items()):
                        sink = sinks.get(identity)
                        if sink is None:
                            continue
                        try:
                            if bool(sink.mute) != original_mute:
                                pulse.sink_mute(sink.index, original_mute)
                            del self._owned[identity]
                        except Exception as exc:
                            self._log(f"[PLAYBACK-MUTE] restore-failed error={type(exc).__name__}")
            except Exception as exc:
                self._log(f"[PLAYBACK-MUTE] connection-failed error={type(exc).__name__}")
            if self._owned:
                self._log(f"[PLAYBACK-MUTE] restore-pending outputs={len(self._owned)}")
            return not self._owned

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._cycle_active or bool(self._owned)


def make_playback_suppressor(base: type, pulse_factory: Callable[..., Any],
                             log: Callable[[str], None]) -> type:
    """Keep native pause/partial ducking and all recording lifecycle callers."""
    class OutputPlaybackSuppressor(base):
        def __init__(self, reduction_percent: float = 50.0) -> None:
            super().__init__(reduction_percent=reduction_percent)
            self._output_mute = SinkMuteGuard(pulse_factory, log)
            self._output_percent = max(0.0, min(100.0, reduction_percent))

        def suppress(self, mode: str = "duck", reduction_percent: float | None = None) -> bool:
            if reduction_percent is not None:
                self._output_percent = max(0.0, min(100.0, reduction_percent))
            if mode not in ("duck", "pause"):
                mode = "duck"
            if mode == "duck" and self._output_percent == 100:
                return self._output_mute.suppress()
            return super().suppress(mode=mode, reduction_percent=self._output_percent)

        def restore(self) -> bool:
            muted_ok = self._output_mute.restore()
            native_ok = super().restore()
            return muted_ok and native_ok

        @property
        def is_active(self) -> bool:
            return self._output_mute.is_active or super().is_active

    return OutputPlaybackSuppressor


def install_playback(upstream: Any) -> None:
    """Install after upstream hash validation, before its application is built."""
    from pulsectl import Pulse

    upstream.PlaybackSuppressor = make_playback_suppressor(
        upstream.PlaybackSuppressor, Pulse, upstream.log,
    )
    upstream.log("[PLAYBACK-MUTE] enabled mode=duck percent=100 volume-writes=none")
