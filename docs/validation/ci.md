# Source installer validation

The workflow validates a candidate and retains a source installer artifact.
It does not create tags, publish a GitHub Release, deploy the application,
change a desktop service or read provider credentials. A local run is not a
successful GitHub Actions run.

## Remote status and test artifacts

Use the [Validate source installer workflow](https://github.com/infantesromeroadrian/arca-dicta/actions/workflows/ci.yml)
to inspect the live result for the exact commit being evaluated. This document
does not assert a remote PASS or copy a changing workflow status into source.
The run's commit, conclusion and uploaded checksums identify its result.

A successful run retains `source-installer-<commit SHA>` for 14 days, containing
the source tarball, checksum and generated manifest. The separate
`arch-validation-<commit SHA>` artifact contains validation evidence. These are
downloadable development test artifacts; they are not a GitHub Release or an
accepted alpha. The [installation guide](../installation.md) also describes
building an archive directly from source.

## Recorded local evidence, 2026-10-16

The E18 local Arch run passed **177 tests**, Ruff, ShellCheck, all **26 upstream
contracts**, and two byte-identical archive builds. The frozen artifact used
for the clean-user D1 check and the later E22 service roundtrip was:

| Artifact | SHA-256 |
| --- | --- |
| `arca-dicta-0.1.0-alpha.1.tar.gz` | `7f9e6318f62360da6c5fe429beb667c87c8f5e940777e2b852eec287bfcb7862` |
| `release-manifest.json` | `0de58f628ac2964e572c31616dabb2bad1a806b65eccaf085ff1ef97211c7033` |

Its manifest covers 72 source files. D1 used a fresh container user (UID 1001),
the native package prerequisites, a fresh model download and the real installer.
Installation, doctor 30/30, synthetic espeak-ng speech inference through the
local worker, idempotent preparation and uninstall passed. The foreign and
modified files described below were preserved. No microphone, physical desktop
or cloud provider was involved; synthetic speech inference does not measure
physical dictation quality or latency.

E22 subsequently exercised real service activation (doctor 35/35) and recovery
of all 25 baseline files and modes, with mute/volume unchanged on four sinks.
The [service recovery record](installer-baseline.md#e22-real-activation-and-recovery-2026-10-16)
states its precise boundary. Package validation still needs physical dictation
in an editor and browser, failure and cancellation checks, audible playback
restoration, latency measurements and a demo. The first GitHub Release remains
pending.

These are historical results for the hashes above. Documentation changes also
change the archive identity: later builds carry their own generated manifest
and checksum. Consult the corresponding Actions run for remote CI evidence;
local success is not a remote CI result.

## Inputs and boundaries

- GitHub-hosted `ubuntu-24.04` runs the Docker CLI. Checkout and upload actions
  are fixed to full commit IDs, with `contents: read` and checkout credential
  persistence disabled. There is no `pull_request_target` or remote publishing job.
- The container is the official Arch Linux x86_64 image
  `sha256:363129b67b2bc6c5a634f8fb4961a7e2eba213a2ec075d159127b1260290a722`
  (base `20261011.0.609700`). The repository mount is read-only. Only the
  designated output directory is writable on the host; no home, device,
  desktop session, Docker socket or provider configuration is mounted.
- The container performs the supported full `pacman -Syu` upgrade. The image
  digest fixes the base, **not the rolling package repositories**. Exact
  installed packages and Python/Ruff/ShellCheck versions are retained with
  each run. Tests run as an unprivileged container user. A disposable venv
  installs `requirements/daemon.lock` with uv, required hashes and no source
  builds; GI/Cairo/evdev come from native Arch packages through
  `--system-site-packages`. PyPI dependencies are never installed globally.
- `tools/ci/prepare_upstream.py` downloads hyprwhspr v1.49.0, requires archive
  SHA-256 `b1e552bb3be43438d3080a4dc1c6d550acb8860582f45f8ff353b35d29b5263c`,
  then verifies all 12 core and 14 HUD compatibility entries. This verifies
  the pinned source contract, not the identity of an installed Arch package.
- The suite uses synthetic provider, clipboard, capture and Pulse fixtures.
  Cairo/Pango/GTK import and layout checks run without starting a GUI. Passing
  CI does not prove physical audio, cloud transcription, local inference,
  desktop appearance, keyboard delivery, service activation or latency.

Official sources checked on 2026-10-16:
[Arch image recipe](https://github.com/docker-library/official-images/blob/1e7d9a2d495ffe76934a775b6bd68ac892a14c1b/library/archlinux),
[Arch container guidance](https://gitlab.archlinux.org/archlinux/archlinux-docker/-/blob/master/README.md),
[checkout v7.0.1](https://github.com/actions/checkout/tree/3d3c42e5aac5ba805825da76410c181273ba90b1),
[upload-artifact v7.0.2](https://github.com/actions/upload-artifact/tree/cf430e030ddbb5b0abf93d22962f4752f3646cd9),
[hyprwhspr source](https://github.com/goodroot/hyprwhspr/tree/88ef652a974854fefdd1ef2bfc9c79ae31c52008).
The pinned actions use Node 24; checkout documents runner 2.327.1 or newer.
The workflow uses GitHub-hosted runners and does not install a custom runner.

## Reproduce locally

Only run the bootstrap script in the disposable container. It installs
packages into that container. The following command does not install host
packages or mount the user's home:

```sh
ci_output=$(mktemp -d /tmp/arca-dicta-ci-output.XXXXXXXX)
docker run --rm --name arca-dicta-ci-local \
  --security-opt no-new-privileges \
  --mount "type=bind,src=$PWD,dst=/src,readonly" \
  --mount "type=bind,src=$ci_output,dst=/out" \
  docker.io/library/archlinux@sha256:363129b67b2bc6c5a634f8fb4961a7e2eba213a2ec075d159127b1260290a722 \
  bash /src/tools/ci/run.sh /out
```

## Reproducible archive

```sh
python -B -m unittest test_release
python -B tools/build_release.py --output /tmp/arca-dicta-release
```

Use a fresh output directory. Output inside the checkout is allowed only
under ignored `dist/`. The builder reads exact paths from
`packaging/release-files.txt`; it never sweeps the worktree. Every listed file
is mandatory. Symlinks, special files, duplicate/unsafe paths, runtime data,
unsupported versions and existing output artifacts are rejected.

The archive contains `arca-dicta-<VERSION>/` and a generated
`release-manifest.json` of the form
`{"version":"0.1.0-alpha.1","files":{"relative/path":"sha256"}}`.
The manifest covers every allowlisted file and excludes itself. The same
manifest is emitted next to the archive. A `.tar.gz.sha256` file verifies the
complete archive, including the manifest. These hashes detect corruption;
they are not a publisher signature or attestation.

Tar entries are sorted, timestamps/owner IDs are zero, owner names are empty,
directories are 0755, entrypoint scripts are 0755 and other files are 0644.
The gzip timestamp is zero and its original filename is empty. File mtimes,
checkout path and source permission bits do not affect output. CI builds
twice and compares bytes. Reproduction assumes the same source content and
Python/zlib compression implementation; installed package sets are recorded.

The archive includes source, dependency locks, model metadata, bilingual
installation documentation, licenses, architecture sources/view and tests.
Earlier extraction evidence under `docs/provenance/` and `docs/validation/`
is retained with its historical scope; `release-manifest.json` identifies
the current archive contents.
It contains no interpreter, environment, model weights or local runtime state.
The installer verifies the manifest before preparing its user-owned runtime.

## Opt-in clean user installation

`tools/ci/clean_install.sh` exercises the real installer separately from the
ordinary CI job. It downloads the pinned model afresh (approximately 484 MB),
so it is not run for every pull request. Supply the final archive through a
read-only mount and keep the output outside the repository:

```sh
clean_output=$(mktemp -d /tmp/arca-dicta-clean-output.XXXXXXXX)
artifact_dir=/absolute/directory/containing/the/final/archive
docker run --rm --name arca-dicta-ci-clean \
  --security-opt no-new-privileges \
  --mount "type=bind,src=$PWD,dst=/src,readonly" \
  --mount "type=bind,src=$artifact_dir,dst=/artifact,readonly" \
  --mount "type=bind,src=$clean_output,dst=/out" \
  docker.io/library/archlinux@sha256:363129b67b2bc6c5a634f8fb4961a7e2eba213a2ec075d159127b1260290a722 \
  bash /src/tools/ci/clean_install.sh all /artifact/arca-dicta-0.1.0-alpha.1.tar.gz
```

The script builds the reviewed, pinned AUR recipes for hyprwhspr 1.49.0-1 and
its two AUR-only dependencies (`python-pulsectl`, `python-sounddevice`) as an
unprivileged build user. Recipes and upstream archives have fixed integrity
checks; pacman installs packages without bypassing dependency checks. This
creates a new package artifact from the pinned source, not the byte identity
of a pre-existing developer package. Exact recipe commits/hashes, built package
hashes and the complete installed package inventory are retained in the output.

A different, newly created user then extracts the final source archive,
installs with a fresh model download, runs `doctor --no-service`, sends
synthetic espeak-ng speech through the real local worker using anonymous
pipes, repeats installation, and uninstalls. It verifies preservation of five
synthetic foreign config/credential/binding files, an edited owned file, an
unknown user file, an empty user directory and the native launcher. Reports
contain counts, status and hashes, without audio or transcript content.

The proof records the exact archive and manifest SHA-256. A passing result
demonstrates clean Arch userspace preparation with a new user. It does not
demonstrate a second physical machine, service activation, GUI, microphones,
cloud providers, audible restoration or physical latency. Those remain
separate acceptance boundaries. `prepare` and `exercise <archive>` are also
available for two stages in the same temporary container; no long-lived
service or host unit is created.
