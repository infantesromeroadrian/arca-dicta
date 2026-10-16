# Offline extraction evidence

Executed on 2026-10-16. Scope: the isolated F1 development candidate, using
the already installed native library and runtimes. No packages were installed.
Host conditions and all pending physical scenarios are in
[scenarios.md](scenarios.md). The fixed bytes are identified by
[candidate-sha256.json](../provenance/candidate-sha256.json).

## Executed checks

| Check | Command / method | Observed result |
| --- | --- | --- |
| Preserved strict baseline | From the preserved snapshot: `python -B -m unittest test_preview test_playback test_review test_review_gate test_hud` | 99 tests passed in 11.855 s. |
| Baseline lint, unconfigured | From the preserved snapshot: `ruff check --no-cache .` | Five pre-existing E402 reports in `hud_runtime/mic_osd/hud.py`; no other reports. GObject version selection must precede GTK imports. |
| Portability additions after P2 correction | `python -B -m unittest test_config` | 17 tests passed in 0.152 s. |
| Complete corrected candidate | `python -B -m unittest discover` | 116 tests passed in 12.022 s. All 99 baseline test method names retained. |
| Candidate lint | Initial tree: `ruff check --no-cache .`; corrected modules: `ruff check --no-cache main.py test_config.py` | Both passed with Ruff 0.15.17. Only the GObject file's E402 is excluded; the local HUD package has one explained E402 import annotation after adding its source root. |
| Shell entrypoints | `shellcheck launch review-hook` | Passed with ShellCheck 0.11.0. |
| Python syntax | `compile(path.read_bytes(), str(path), 'exec')` | Initial 18 modules compiled in memory; both changed modules recompiled after the P2 correction. No bytecode or cache written. |
| Core/HUD compatibility | AST-read `SUPPORTED_FILES` in preserved and extracted sources; compare values and installed file SHA-256 | 12 core and 14 HUD hash entries unchanged. Native contract tests passed on hyprwhspr 1.49.0-1. |
| Source provenance | Rehash each preserved source and compare each destination | 22 source files inventoried; 11 destination files byte-identical, 11 adapted. |
| Repository boundaries | Enumerate text files and directories; scan for personal absolute paths, private-key blocks and credential-shaped values | No matches; no environments, weights, recordings, transcripts, runtime state or cache directories. Synthetic credential fixtures are labelled and used only with fake provider boundaries. |

These runtimes are measured test execution durations, not dictation latency.
No type checker was added or run: mypy was not installed. A passing lint check
does not claim full static typing or cross-platform support.

Reproduce the in-memory syntax check from the repository root:

```sh
python -B - <<'PY'
from pathlib import Path
paths = sorted(Path('.').rglob('*.py'))
for path in paths:
    compile(path.read_bytes(), str(path), 'exec')
print(f'Compiled {len(paths)} Python files without writing bytecode')
PY
```

## What the offline evidence proves

The suite covers bounded capture tails and protocol frames, cancellation and
generation invalidation, local worker environment isolation, strict review
failure/timeout handling, one pending retry/discard, no duplicate delivery,
FIFO finalization, native push-to-talk dispatch, fake sink identity/restoration,
and Cairo/Pango layout without a visible window. The additional tests cover
explicit dependency paths, missing config before desktop import, shell CLI
delegation, different working directories, spaces, and upstream path propagation
to the HUD/native loader. Provider calls and clipboard/audio boundaries are
fakes; the timeout test executes only a synthetic subprocess.

The source has not been installed or activated. Real local inference, provider
availability, speech accuracy, desktop appearance, keyboard/clipboard behavior,
audible sink restoration and both requested latency distributions remain
unmeasured for this candidate. No dependency/model contents are validated merely
by checking a path. The operating service was outside the mutation scope.

## Verify the fixed candidate

The manifest excludes `.git` and itself. It includes every other candidate file,
including documentation and licenses, with SHA-256 and executable mode. Run:

```sh
python -B - <<'PY'
from pathlib import Path
import hashlib
import json
manifest_path = Path('docs/provenance/candidate-sha256.json')
manifest = json.loads(manifest_path.read_text())
expected = {item['path'] for item in manifest['files']}
actual = {p.as_posix() for p in Path('.').rglob('*')
          if p.is_file() and '.git' not in p.parts and p != manifest_path}
assert actual == expected, 'Candidate file set changed'
for item in manifest['files']:
    path = Path(item['path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256'], item['path']
    assert oct(path.stat().st_mode & 0o777) == item['mode'], item['path']
print(f"Verified {len(expected)} fixed files")
PY
```

## Protected review and focused correction

Protected independent review of the first fixed manifest
`3c9632c7188c4ace72b26637171c531dff54b4f0ff8943bf48646a5c8cc13d76`
identified one P2 regression: direct `python -B main.py --help` resolved
`ARCA_DICTA_UPSTREAM_LIB` during import, before delegating CLI arguments.
An empty or relative library setting therefore prevented delegation; the shell
`launch` entrypoint already delegated correctly.

The added regression test starts a fresh Python process and a synthetic CLI
target. Before correction it reproduced both failures (exit 1 instead of the
synthetic target's exit 23). After correction, library resolution occurs only
when the daemon's compatibility check runs, after the CLI branch. All three
fresh-process cases (setting absent, empty and relative) preserve arguments and
the target exit code. The existing override test now checks the actual path
used by the compatibility guard, alongside the HUD loader paths.

Only `main.py` and `test_config.py` changed in this correction, plus this
evidence and the two hash inventories. The full suite was repeated because
module initialization is shared by the core/playback tests. Review, preview,
audio and HUD implementation files remain unchanged. No second broad audit,
service activation, provider call, publication or deployment was performed.
The correction is supported by the focused reproduction and the 116-test
result; the earlier independent review is not presented as a review of bytes
that did not yet exist.
