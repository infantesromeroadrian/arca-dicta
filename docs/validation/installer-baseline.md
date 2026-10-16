# Native installer baseline

Captured on 2026-10-16 before any installer activation. This record describes
the working personal service separately from the portable candidate.
The original capture below is retained as historical evidence. The later
activation and recovery result is appended at the end of this document.

## Current service

The sole user unit is `hyprwhspr.service`. Its effective process was checked
against the existing personal runtime entrypoint: active/running, zero automatic
restarts. The unit has a pre-existing personal service file and
`20-local-preview.conf` drop-in. Their contents and secret references are not
published here.

The operational runtime is not asserted to be a Git deployment. All 22 source
files were checked against the preserved strict baseline, allowing only the
two already deployed review fixes:

| File | Current SHA-256 |
| --- | --- |
| `review.py` | `3fde9b3491bb745c31ece18ff24028bf74c01287d7fd994504115d3d4914e75a` |
| `review_gate.py` | `b67f8ac4d968e0e66b139ab108c701eebd461184b36b39b02b1aa77168b40221` |

The remaining 20 files match that earlier baseline. The configuration retains
SHA-256 `e0ec27133d046c68f104ebdaac6ca90a0bc9b5674d7a9488102ed51e28514339`.
The earlier false-rejection fix remains operational; its previous physical
confirmation is documented in [retained-review.md](retained-review.md).

Observed host versions: hyprwhspr 1.49.0-1, Python 3.14.8-1, GTK4
4.24.1-1.1, PyGObject 3.58.1-1, pycairo 1.29.2-1.1, PipeWire 1.6.9-1,
WirePlumber 0.5.18-1.1 and Hyprland 0.56.2-4.1. The effective daemon interpreter
reports Python 3.14.8, NumPy 2.5.4, sounddevice 0.5.6 and soxr 1.1.0.
These observations are not a compatibility promise for other installations.

## Recovery boundary

A new private snapshot preserves **25 files**: the 22 current runtime sources,
native configuration, service file and existing drop-in. Every copy is
byte-identical; original modes and hashes are recorded. The snapshot directory
is owner-only and its copied files are read-only. It stays outside Git under
the local validation state directory.

Recovery must preserve the two current review fixes. The older snapshot from
before that correction is not the rollback target for installer activation.
The installer must preserve existing overrides and secret configuration,
change only its owned drop-in and managed settings, and operate on the same
unit. Upstream skips its ordinary instance lock under systemd, so starting
a second dictation unit is not an acceptable test strategy.

Snapshot integrity is verified. A recovery rehearsal restored all 25 files to
an isolated temporary directory and verified both bytes and original modes.
The live service was untouched. Installer transaction tests, a real candidate
activation, physical validation and the final service rollback check remain
pending. This file recovery rehearsal does not demonstrate a service rollback.

## Candidate and acceptance

Source preparation starts from published commit
`1cf0e6432c460ad4f1603df6392e796a10879b52`. New installer code, dependency
resolution and the downloadable artifact will be identified separately once
fixed. No installer deployment or release is claimed by this baseline.

The current service must supply the physical baseline before candidate
activation. Both paths need the same observer and conditions. CI, synthetic
model inference and clean-user container installation cannot replace the
microphone, visible HUD, actual paste and audible restoration checks.

## E22: real activation and recovery, 2026-10-16

The frozen development artifact identified in [the CI record](ci.md#recorded-local-evidence-2026-10-16)
completed a real activation/deactivation roundtrip on the existing user service.
Before activation, all 72 source hashes and all 25 live baseline files and modes
matched; the prepared prefix passed doctor 30/30. Reduced state checks showed
Idle, no retained-state signal or preview, no capture connection and one daemon.
No process memory or dictation text was inspected.

`activate --adopt-existing` succeeded. The one `hyprwhspr.service` ran the
candidate entrypoint and its prepared interpreter, active/running with zero
automatic restarts. Doctor passed 35/35. `deactivate` then restored the personal
entrypoint, original unit and existing drop-in. All 25 files matched their
original hashes and modes, including the two review fixes recorded above.
The service was again active/running and Idle, with one daemon and zero
automatic restarts. Mute and per-channel volume were unchanged on all four
output sinks. The owned drop-in and activation journal were absent; the
prepared prefix remained available and passed doctor 30/30.

The 72 frozen source hashes, tarball and manifest were unchanged after the
roundtrip. Private evidence contains only states, counters, hashes and numeric
audio metadata. The check initiated no recording, inference, provider request
or observer process. Unchanged mixer metadata is not a physical audible test.

The user accepted the separate personal runtime for personal use (E21). Package
validation still needs physical dictation in an editor and browser, failure and
cancellation checks, audible playback restoration, latency measurements and a
demo. The first GitHub Release remains pending. Documentation
updates after this record produce a separately identified artifact; the original
hashes remain historical evidence rather than a claim about later builds.
