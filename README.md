# ARCA Dicta

Technical dictation for Hyprland, developed as an extension to
[hyprwhspr](https://github.com/goodroot/hyprwhspr).

**Status: extracted development candidate (F1). There is no installable release yet.**

The extracted prototype combines local provisional previews, final
transcription through Groq, mandatory bilingual text review through Ollama,
a readable HUD, and reversible playback muting. Personal runtime paths have
been replaced by explicit configuration. Installation, distribution and a
physical end-to-end check of this candidate remain pending.

The initial target is Arch/CachyOS with Hyprland on x86_64. Desktop integration
is native. This extraction includes no installer, service unit, container,
model weights, Python environment or provider credentials.

## Development configuration

The layout stays flat to preserve the reviewed imports and adapters. Copy the
values in [.env.example](.env.example) into your own caller environment;
`launch` does not source an environment file or discover personal caches.

| Variable | Contract |
| --- | --- |
| `ARCA_DICTA_PYTHON` | Required absolute executable path to a prepared daemon Python runtime; used by `launch`. |
| `ARCA_DICTA_WORKER_PYTHON` | Required absolute executable path to a prepared local inference runtime. |
| `ARCA_DICTA_MODEL_DIR` | Required absolute directory containing the existing local `small` model, including `model.bin`, `config.json`, `tokenizer.json`. |
| `ARCA_DICTA_UPSTREAM_LIB` | Optional installed library path; defaults to `/usr/lib/hyprwhspr/lib`. Applies to adapters, HUD and native contract tests. |
| `ARCA_DICTA_UPSTREAM_BIN` | Optional installed CLI path; defaults to `/usr/bin/hyprwhspr`. |

Missing or invalid daemon dependencies stop startup before importing the
desktop application. Paths may contain spaces. File presence is checked;
model provenance, weight contents and inference dependencies still need an
appropriate prepared runtime. Nothing is downloaded or installed.

The preserved local inference baseline is faster-whisper 1.2.1, CTranslate2
4.8.0 and `Systran/faster-whisper-small` revision
`536b0662742c02347bc0e980a01041f333bce120`, CPU/int8, four threads, beam 1,
VAD and `local_files_only=True`. The daemon and offline checks also need their
existing numpy and GTK/Cairo/PyGObject dependencies; see
[dependency boundaries](docs/provenance/extraction.md).

The native configuration must select `rest-api`, final Groq
`whisper-large-v3-turbo`, language autodetection, and the exact adjacent
`review-hook` as `post_transcription_hook`, using shell quoting when needed:

```json
{"post_transcription_hook": "exec '/absolute/path/to/ARCA Dicta/review-hook'"}
```

This is a configuration example, not an installation command. The extraction
does not update the native configuration or redirect the running service.
The reviewer uses only `OLLAMA_API_KEY` or `OLLAMA_CLOUD_API_KEY`, inherited
by the daemon from its existing secret-management boundary. Never put real
credentials in this repository.

## Preserved behavior and boundaries

- The preview reuses the existing capture, takes at most eight seconds of
  recent PCM, requests its first snapshot at 1.2 seconds and leaves at least
  two seconds between inference starts. These are scheduling settings, not
  measured time to first text. Stop/cancel invalidate late results.
- The worker receives an allowlisted environment without provider keys. It
  opens no microphone and keeps audio/results in bounded anonymous pipes.
  A preview failure disables preview for that process; the final strict
  dictation path remains available.
- Final STT remains upstream Groq. Mandatory review is
  `deepseek-v4.1-flash` through Ollama Cloud; failed review never copies or
  pastes raw text. One pending dictation stays in memory for retry or discard.
  See [review and recovery](REVIEW.md).
- Twelve core file hashes pin the private hyprwhspr 1.49.0-1 contract.
  Core drift stops this adapted daemon; it does not fall back to an unreviewed
  daemon. Fourteen additional hashes protect the local HUD adapter. A HUD
  failure can use the native visual renderer while the review gate remains
  mandatory. Hashes are not updated automatically.
- The HUD uses a dark 560 × 124 panel with 17 px provisional text and retained
  review states. See [HUD](HUD.md). Full suppression mutes existing output
  sinks temporarily, with ownership and identity guards; partial ducking and
  pause remain native. See [playback](PLAYBACK.md).
- Every argument passed to `launch` is delegated to the installed CLI without
  requiring daemon/worker/model configuration. **The strict gate applies to
  the adapted daemon, not a direct native CLI invocation.** FIFO stop/release
  is asynchronous only for auto/toggle; native push-to-talk stays synchronous.

The extension adds no persistent transcript or audio history. The HUD reuses
the native, temporary 0600 preview file under `$XDG_RUNTIME_DIR/hyprwhspr`;
that IPC file is removed on completion/cancel and is not a transcript archive.

## Offline checks

With the compatible native library and its Python dependencies already
available, from this directory:

```sh
python -B -m unittest discover -v
ruff check --no-cache .
shellcheck launch review-hook
```

These tests use synthetic data, fake providers/Pulse/clipboard boundaries and
offscreen Cairo/Pango rendering. They do not start the service, load weights,
capture audio, paste into the desktop or call a provider. Results and limits:
[offline evidence](docs/validation/offline.md),
[future physical scenarios](docs/validation/scenarios.md).

## Project tracking

The canonical plan is maintained in Obsidian at
`30-Proyectos/ARCA-Dicta/plan.md`. It contains requirements, phases, tasks,
acceptance criteria and evidence. This repository does not duplicate that plan.

The source came from the preserved strict-review candidate identified in
[provenance](docs/provenance/extraction.md), with a 22-file
[source inventory](docs/provenance/source-inventory.json). The running personal
runtime remains separate. No credentials, recordings, model weights or private
logs belong in this repository.

## Upstream attribution

hyprwhspr provides the underlying desktop dictation application and is
[MIT licensed](https://github.com/goodroot/hyprwhspr/blob/main/LICENSE).
The exact compatibility baseline is hyprwhspr 1.49.0-1. Its full license notice
is retained in [LICENSES/hyprwhspr-MIT.txt](LICENSES/hyprwhspr-MIT.txt); see
[third-party notices](THIRD_PARTY_NOTICES.md).

ARCA Dicta's contribution is licensed under the [MIT License](LICENSE),
Copyright (c) 2026 ARCA Dicta contributors. Third-party rights and notices
remain separate. Model redistribution has not been approved.
