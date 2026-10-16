# F1 extraction and recovery boundary

Date: 2026-10-16. This is a local development candidate, not a release,
installation or deployment. The canonical project plan remains the existing
Obsidian `30-Proyectos/ARCA-Dicta/plan.md`; this document records source evidence.

## Source of truth

The sole extraction source is the preserved maintenance snapshot
`20261016-handy-hyprwhspr/strict-review-1335/candidate`. Its 22 files were
verified by the coordinator against the deployed runtime and the deployment
manifest before extraction. The separate, older permissive `backup` snapshot
was not used. Full private paths, deployment-manifest contents, credentials,
provider benchmarks and maintenance logs are not exported.

[source-inventory.json](source-inventory.json) records every relative source
path, SHA-256, category, destination and disposition. The preserved source
stays outside this checkout; it was not modified by extraction or testing.
The bootstrap README was reconciled with the extracted behavior and project
tracking information. The existing `.gitignore` was preserved byte-for-byte.

## Minimal adaptation

| Surface | Extraction change |
| --- | --- |
| `launch`, `main.py`, `dicta_config.py` | Explicit daemon/worker interpreter and local model paths; relative entrypoint resolution; validation before importing the native desktop application. No automatic cache, interpreter or model discovery. |
| `hud_bridge.py`, `hud_runtime/mic_osd/{__init__,main}.py` | Shared configurable upstream library location, including the native renderer loader. |
| `test_playback.py`, `test_review_gate.py` | Read the same configured upstream library; quote the synthetic hook configuration correctly when a checkout path contains spaces. Existing test cases remain. |
| Component docs and README | Remove personal paths and private historical measurements; correct the obsolete five-hash/permissive-fallback description to twelve core hashes and strict stop. |
| `test_config.py`, `ruff.toml` | Focused portability checks and one documented GObject import-order exception. |

One intentional startup behavior change is necessary for portability: missing
or invalid required runtime paths stop this candidate early with a named
configuration error. The former personal-path lookup could proceed with an
unavailable preview. Failures after valid configuration still preserve the
existing preview failure behavior; the review gate remains mandatory.

CLI arguments continue to delegate upstream without requiring daemon/model
configuration. `HYPRWHSPR_ROOT` is derived from the configured library parent
for daemon startup. The `review-hook` keeps its system `/usr/bin/python3`
stdlib-only, isolated invocation. The target remains Arch/CachyOS; this is
path portability, not a claim of cross-platform support or a relocatable
native dependency bundle.

The core 12-file and HUD 14-file hash contracts are preserved. No prompt,
quality parameter, state machine, review policy, retry policy, audio mute
logic or inference setting was changed. A core mismatch stops the adapted
daemon. A HUD-only mismatch can fall back to the native visual renderer while
the strict review gate stays in force. Direct native CLI use is outside that
gate guarantee.

## Dependencies excluded from extraction

| Dependency | Required boundary / removal |
| --- | --- |
| Personal daemon virtual environment | Not copied. Caller supplies `ARCA_DICTA_PYTHON` with native hyprwhspr dependencies. |
| Personal inference virtual environment | Not copied. Caller supplies `ARCA_DICTA_WORKER_PYTHON`. Observed faster-whisper 1.2.1, CTranslate2 4.8.0, numpy and PyAV stay external. |
| Hugging Face cache and weights | Not copied or discovered. Caller supplies `ARCA_DICTA_MODEL_DIR`; source baseline is small revision `536b0662742c02347bc0e980a01041f333bce120`. Presence checks do not attest weight hashes. |
| Installed hyprwhspr | External 1.49.0-1 native library and binary. Its private files are hash checked; its modules are delegated rather than vendored. |
| Desktop/audio stack | External Hyprland, GTK/Pango/Cairo/PyGObject, layer-shell, PipeWire/Pulse and pulsectl. No new service or device is provisioned. |
| Credentials and provider configuration | Not copied or read by extraction. Final STT uses native Groq configuration; review reads only the two documented Ollama environment names. Preview uses an environment allowlist. |
| Operational state | No unit/drop-in, secret store, runtime IPC, capture, clipboard, private log or transcript is imported. |

See [third-party notices](../../THIRD_PARTY_NOTICES.md) for licensing limits.
The project's own contribution has the authorized MIT license; the retained
hyprwhspr MIT notice covers its separate provenance. No installable dependency
lock or support matrix is claimed in F1. Licensing of future bundled weights
or runtime dependencies is outside this extraction's completed scope.

## Recovery and rollback

F1 does not point the service at this checkout and does not edit its
configuration. There is therefore no service rollback to execute for this
extraction: the operating runtime stays at its original location. No service
restart, Git staging, commit, remote publication or deployment was performed.

To recover the extraction baseline, resolve the exact preserved strict
snapshot named above through the local maintenance record and verify all 22
hashes against the inventory before copying into a separate authorized target.
Do not substitute the older permissive backup and do not remove only the hook
to bypass strict review. A future activation must preserve its own exact
service/config baseline and define an authorized restoration procedure.

The development candidate is fixed by the repository-relative hashes in
[candidate-sha256.json](candidate-sha256.json). That manifest excludes itself
to avoid circular hashing. Verification results apply only to those bytes.
