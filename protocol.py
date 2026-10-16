"""Bounded in-memory protocol shared with the local preview process."""

from __future__ import annotations

import json
import struct
from typing import BinaryIO

MAX_JSON_BYTES = 8192
MAX_TEXT_CHARACTERS = 768
MAX_WINDOW_SECONDS = 8
MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 192000
MAX_AUDIO_BYTES = MAX_WINDOW_SECONDS * MAX_SAMPLE_RATE * 4


class PreviewFailure(Exception):
    """A stable failure code; never includes audio, text or provider errors."""


def encode_json(message: dict) -> bytes:
    return json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def decode_json(payload: bytes) -> dict:
    if not 0 < len(payload) <= MAX_JSON_BYTES:
        raise PreviewFailure("invalid-json-size")
    try:
        message = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise PreviewFailure("invalid-json") from exc
    if not isinstance(message, dict):
        raise PreviewFailure("invalid-message")
    return message


def frame(payload: bytes, maximum: int) -> bytes:
    if not 0 < len(payload) <= maximum:
        raise PreviewFailure("invalid-frame-size")
    return struct.pack("!I", len(payload)) + payload


def read_exact(stream: BinaryIO, size: int) -> bytes:
    parts = bytearray()
    while len(parts) < size:
        part = stream.read(size - len(parts))
        if not part:
            raise EOFError
        parts.extend(part)
    return bytes(parts)


def read_frame(stream: BinaryIO, maximum: int) -> bytes:
    size = struct.unpack("!I", read_exact(stream, 4))[0]
    if not 0 < size <= maximum:
        raise PreviewFailure("invalid-frame-size")
    return read_exact(stream, size)


def write_json(stream: BinaryIO, message: dict) -> None:
    stream.write(frame(encode_json(message), MAX_JSON_BYTES))
    stream.flush()


def validate_request(message: dict) -> tuple[int, int, int, str | None]:
    if set(message) != {"request", "samples", "sample_rate", "language"}:
        raise PreviewFailure("invalid-request")
    request, samples, sample_rate = (
        message["request"],
        message["samples"],
        message["sample_rate"],
    )
    if any(type(value) is not int for value in (request, samples, sample_rate)):
        raise PreviewFailure("invalid-request-number")
    if not 0 < request < 2**63:
        raise PreviewFailure("invalid-request-number")
    if not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        raise PreviewFailure("invalid-sample-rate")
    if not 0 < samples <= sample_rate * MAX_WINDOW_SECONDS:
        raise PreviewFailure("invalid-audio-size")
    language = message["language"]
    if language is not None and (
        not isinstance(language, str)
        or not 2 <= len(language) <= 3
        or not language.isascii()
        or not language.isalpha()
        or not language.islower()
    ):
        raise PreviewFailure("invalid-language")
    return request, samples, sample_rate, language
