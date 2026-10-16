"""A single bounded preview job, isolated from recording and final transcription."""

from __future__ import annotations

import os
import selectors
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from protocol import (
    MAX_AUDIO_BYTES,
    MAX_JSON_BYTES,
    MAX_SAMPLE_RATE,
    MAX_TEXT_CHARACTERS,
    MAX_WINDOW_SECONDS,
    MIN_SAMPLE_RATE,
    PreviewFailure,
    decode_json,
    encode_json,
    frame,
    validate_request,
)


@dataclass(frozen=True, slots=True)
class AudioSnapshot:
    audio: np.ndarray
    sample_rate: int


@dataclass(frozen=True, slots=True)
class PreviewResult:
    text: str
    decode_ms: int


@dataclass(frozen=True, slots=True)
class PreviewSession:
    token: object
    generation: int
    language: str | None
    started_at: float
    stopped: threading.Event = field(default_factory=threading.Event)


def capture_tail(
    app, token: object, seconds: float = MAX_WINDOW_SECONDS
) -> AudioSnapshot | None:
    """Reference a bounded tail under the audio lock, then concatenate outside it.

    hyprwhspr 1.49 appends independent mono float32 arrays and never mutates
    them afterwards. Keeping those references remains safe across buffer reset.
    """
    if not 0 < seconds <= MAX_WINDOW_SECONDS:
        raise ValueError("preview window out of range")
    capture = app.audio_capture
    chunks = []
    with capture.lock:
        if (
            not capture.is_recording
            or not app.is_recording
            or app._recording_session is not token
        ):
            return None
        rate = capture.sample_rate
        if type(rate) is not int or not MIN_SAMPLE_RATE <= rate <= MAX_SAMPLE_RATE:
            raise PreviewFailure("unsupported-capture-rate")
        remaining = int(seconds * rate)
        for chunk in reversed(capture.audio_data):
            if (
                not isinstance(chunk, np.ndarray)
                or chunk.ndim != 1
                or chunk.dtype != np.float32
            ):
                raise PreviewFailure("unsupported-capture-buffer")
            count = min(len(chunk), remaining)
            if count:
                chunks.append(chunk[-count:])
                remaining -= count
            if remaining == 0:
                break
    if not chunks:
        return None
    audio = np.concatenate(chunks[::-1])
    if not np.isfinite(audio).all():
        raise PreviewFailure("invalid-capture-values")
    return AudioSnapshot(audio, rate)


def worker_environment() -> dict[str, str]:
    """An allowlist, not a copy of the daemon's credential-bearing environment."""
    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "OMP_NUM_THREADS": "4",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "4",
    }


class LocalDecoder:
    """One persistent process; each exchange has a deadline and a byte limit."""

    def __init__(self, python: Path, model: Path, worker: Path) -> None:
        self._python, self._model, self._worker = python, model, worker
        self._process = None
        self._process_lock = threading.Lock()
        self._closed = False
        self._reaping = False
        self._request = 0

    def start(self) -> None:
        with self._process_lock:
            if self._closed:
                raise PreviewFailure("worker-closed")
            self._process = subprocess.Popen(
                [str(self._python), "-B", str(self._worker), str(self._model)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=worker_environment(),
                bufsize=0,
                close_fds=True,
            )
            os.set_blocking(self._process.stdin.fileno(), False)
            os.set_blocking(self._process.stdout.fileno(), False)
        if self._read_message(time.monotonic() + 15) != {
            "kind": "ready",
            "protocol": 1,
        }:
            raise PreviewFailure("worker-start-failed")

    def _read_exact(self, size: int, deadline: float) -> bytes:
        stream = self._process.stdout
        data = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_READ)
            while len(data) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise PreviewFailure("worker-timeout")
                part = os.read(stream.fileno(), size - len(data))
                if not part:
                    raise PreviewFailure("worker-eof")
                data.extend(part)
        return bytes(data)

    def _read_message(self, deadline: float) -> dict:
        size = struct.unpack("!I", self._read_exact(4, deadline))[0]
        if not 0 < size <= MAX_JSON_BYTES:
            raise PreviewFailure("invalid-response-size")
        return decode_json(self._read_exact(size, deadline))

    def _write(self, payload: bytes, deadline: float) -> None:
        stream = self._process.stdin
        view = memoryview(payload)
        with selectors.DefaultSelector() as selector:
            selector.register(stream, selectors.EVENT_WRITE)
            while view:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise PreviewFailure("worker-timeout")
                written = os.write(stream.fileno(), view)
                if written <= 0:
                    raise PreviewFailure("worker-eof")
                view = view[written:]

    def transcribe(
        self, snapshot: AudioSnapshot, language: str | None
    ) -> PreviewResult:
        self._request += 1
        request = {
            "request": self._request,
            "samples": len(snapshot.audio),
            "sample_rate": snapshot.sample_rate,
            "language": language,
        }
        validate_request(request)
        deadline = time.monotonic() + 20
        self._write(frame(encode_json(request), MAX_JSON_BYTES), deadline)
        self._write(
            frame(snapshot.audio.astype("<f4", copy=False).tobytes(), MAX_AUDIO_BYTES),
            deadline,
        )
        result = self._read_message(deadline)
        if set(result) != {"kind", "request", "text", "decode_ms"}:
            raise PreviewFailure("invalid-response")
        if (
            result["kind"] != "result"
            or type(result["request"]) is not int
            or result["request"] != self._request
        ):
            raise PreviewFailure("mismatched-response")
        text, decode_ms = result["text"], result["decode_ms"]
        if not isinstance(text, str) or len(text) > MAX_TEXT_CHARACTERS:
            raise PreviewFailure("invalid-response-text")
        if type(decode_ms) is not int or not 0 <= decode_ms <= 20000:
            raise PreviewFailure("invalid-response-timing")
        return PreviewResult(" ".join(text.split()), decode_ms)

    def close(self) -> None:
        with self._process_lock:
            self._closed = True
            process = self._process
            if self._reaping or process is None:
                return
            self._reaping = True
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=0.3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            # Concurrent process exit is harmless; cleanup must preserve the app.
            return
        finally:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


class PreviewController:
    """Serializes publish and clear; stop never waits for a model invocation."""

    def __init__(
        self,
        decoder,
        snapshot: Callable[[object], AudioSnapshot | None],
        is_active: Callable[[object], bool],
        publish: Callable[[str], None],
        clear: Callable[[], None],
        log: Callable[[str], None],
        *,
        status: Callable[[str], None] | None = None,
        first_delay: float = 1.2,
        interval: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._decoder, self._snapshot, self._is_active = decoder, snapshot, is_active
        self._publish, self._clear, self._log = publish, clear, log
        self._status = status
        self._first_delay, self._interval, self._clock = first_delay, interval, clock
        self._condition = threading.Condition()
        self._session = None
        self._generation = 0
        self._closed = False
        self._disabled = False
        self._thread = threading.Thread(
            target=self._run, name="local-preview", daemon=True
        )
        self._thread.start()

    def begin(self, token: object, language: str | None, started_at: float) -> None:
        with self._condition:
            if self._closed or not self._is_active(token):
                return
            if self._session is not None and self._session.token is token:
                return
            if self._session is not None:
                self._session.stopped.set()
            self._generation += 1
            self._session = PreviewSession(
                token, self._generation, language, started_at
            )
            self._emit_status(
                self._session,
                "preview_unavailable" if self._disabled else "listening",
            )
            self._condition.notify()

    def _emit_status(self, session: PreviewSession, state: str) -> None:
        """Called under the same lock as invalidate; never emits for stale work."""
        if (
            self._status is None
            or self._closed
            or self._session is not session
            or session.generation != self._generation
            or session.stopped.is_set()
            or not self._is_active(session.token)
        ):
            return
        try:
            self._status(state)
        except Exception:
            # Status is decoration; a failed renderer must not disable decoding.
            self._log("[LIVE-PREVIEW] status unavailable")

    def invalidate(self, token: object | None = None) -> None:
        with self._condition:
            if (
                token is not None
                and self._session is not None
                and self._session.token is not token
            ):
                return
            if self._session is not None:
                self._session.stopped.set()
            self._session = None
            self._clear()
            self._condition.notify()

    def close(self) -> None:
        with self._condition:
            self._closed = True
        self.invalidate()
        self._decoder.close()

    def _run(self) -> None:
        try:
            self._decoder.start()
            self._log("[LIVE-PREVIEW] ready backend=local-small cpu_threads=4")
            previous = None
            due = 0.0
            first = True
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: self._closed or self._session is not None
                    )
                    if self._closed:
                        return
                    session = self._session
                if session is not previous:
                    previous, first = session, True
                    due = session.started_at + self._first_delay
                if session.stopped.wait(max(0.0, due - self._clock())):
                    continue
                if not self._is_active(session.token):
                    self.invalidate(session.token)
                    continue
                snapshot = self._snapshot(session.token)
                if snapshot is None or len(snapshot.audio) < snapshot.sample_rate * 0.5:
                    due = self._clock() + 0.2
                    continue
                if session.stopped.is_set() or not self._is_active(session.token):
                    del snapshot
                    continue
                requested_at = self._clock()
                with self._condition:
                    self._emit_status(session, "decoding")
                result = self._decoder.transcribe(snapshot, session.language)
                del snapshot
                with self._condition:
                    if (
                        not self._closed
                        and self._session is session
                        and not session.stopped.is_set()
                        and self._is_active(session.token)
                        and result.text
                    ):
                        self._publish(result.text)
                        timing = (
                            f" first_preview_ms={round((self._clock() - session.started_at) * 1000)}"
                            if first
                            else ""
                        )
                        self._log(
                            f"[LIVE-PREVIEW] generation={session.generation}"
                            f" decode_ms={result.decode_ms}{timing}"
                        )
                        first = False
                    self._emit_status(session, "listening")
                due = max(requested_at + self._interval, self._clock())
        except Exception:
            # A process/model/protocol failure disables preview for this daemon.
            # It must never change recording, final Groq calls or text delivery.
            with self._condition:
                self._disabled = True
                closed = self._closed
                if self._session is not None:
                    self._emit_status(self._session, "preview_unavailable")
                self.invalidate()
            if not closed:
                self._log(
                    "[LIVE-PREVIEW] disabled reason=worker-failure; final transcription unchanged"
                )
        finally:
            self._decoder.close()
