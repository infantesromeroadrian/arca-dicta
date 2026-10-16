"""Local CPU preview worker. Audio and text travel only through anonymous pipes."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from protocol import (
    MAX_AUDIO_BYTES,
    MAX_JSON_BYTES,
    MAX_TEXT_CHARACTERS,
    PreviewFailure,
    decode_json,
    read_frame,
    validate_request,
    write_json,
)


def resample_audio(audio, sample_rate: int):
    """Use the already installed PyAV resampler; Whisper receives mono 16 kHz."""
    if sample_rate == 16000:
        return audio
    import av
    import numpy as np

    source = av.AudioFrame.from_ndarray(
        audio.reshape(1, -1), format="flt", layout="mono"
    )
    source.sample_rate = sample_rate
    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    frames = resampler.resample(source) + resampler.resample(None)
    return np.concatenate([item.to_ndarray().reshape(-1) for item in frames])


def main() -> int:
    # No exception messages or model logs may leak transcript content into journald.
    logging.disable(logging.CRITICAL)
    output = sys.stdout.buffer
    try:
        model_path = Path(sys.argv[1])
        if not model_path.is_dir() or not all(
            (model_path / name).is_file()
            for name in ("model.bin", "config.json", "tokenizer.json")
        ):
            raise PreviewFailure("model-unavailable")
        import numpy as np
        from faster_whisper import WhisperModel

        model = WhisperModel(
            str(model_path),
            device="cpu",
            compute_type="int8",
            cpu_threads=4,
            num_workers=1,
            local_files_only=True,
        )
        write_json(output, {"kind": "ready", "protocol": 1})
    except Exception:
        write_json(output, {"kind": "error", "code": "model-unavailable"})
        return 1

    while True:
        try:
            request, samples, sample_rate, language = validate_request(
                decode_json(read_frame(sys.stdin.buffer, MAX_JSON_BYTES))
            )
            payload = read_frame(sys.stdin.buffer, MAX_AUDIO_BYTES)
            if len(payload) != samples * 4:
                raise PreviewFailure("invalid-audio-size")
            audio = np.frombuffer(payload, dtype="<f4")
            if not np.isfinite(audio).all():
                raise PreviewFailure("invalid-audio-values")
            started = time.monotonic()
            audio = resample_audio(audio, sample_rate)
            segments, _ = model.transcribe(
                audio,
                language=language,
                task="transcribe",
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                without_timestamps=True,
                max_new_tokens=96,
            )
            text = " ".join(" ".join(segment.text for segment in segments).split())
            text = text[-MAX_TEXT_CHARACTERS:]
            write_json(
                output,
                {
                    "kind": "result",
                    "request": request,
                    "text": text,
                    "decode_ms": round((time.monotonic() - started) * 1000),
                },
            )
            # Drop references after every request, including the previous raw PCM.
            del audio, payload, segments, text
        except EOFError:
            return 0
        except Exception:
            write_json(output, {"kind": "error", "code": "decode-failed"})
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
