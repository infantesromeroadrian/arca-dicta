# Install, diagnose and recover

This guide describes the **0.1.0-alpha.1 development snapshot**. Its source
installer can prepare a test installation; **there is no GitHub Release yet**.
Physical acceptance of the package and the demo remain pending.
[Español](installation.es.md).

## Native prerequisites

Use an existing Arch/CachyOS x86_64 Hyprland session with PipeWire/Pulse and
WirePlumber. Install native **hyprwhspr 1.49.0-1**, without changing its private
library files. Its [AUR recipe](https://aur.archlinux.org/packages/hyprwhspr)
was checked at commit `7ea4a07c09656c4c84e6cecc9636e969b3fbe553`; its upstream
source and checksums are recorded in [dependency provenance](provenance/dependencies.md).
ARCA Dicta does not install or upgrade that system package, change permissions
on input devices, or run the upstream setup wizard automatically.

After reviewing the ordinary system update, these additional native packages
provide the ABI and desktop dependencies:

```sh
sudo pacman -Syu --needed python python-gobject python-cairo python-evdev gtk4 gtk4-layer-shell portaudio libsndfile libpulse wl-clipboard wtype uv
```

The alpha locks target **Python 3.14**. `uv >=0.11.14` is required; 0.11.14 and
0.13.1 were exercised. GTK/GI/Cairo and evdev come from native packages. The
daemon's own environment inherits those packages; the worker's environment is
isolated. All pip dependencies have exact versions and hashes, and preparation
requires wheels (`--no-build`). A newer native package is not presumed compatible:
the 12 core and 14 HUD hashes must match.

## Build or obtain a test archive

From the repository root, build into a fresh output directory:

```sh
build_dir=$(mktemp -d /tmp/arca-dicta-candidate.XXXXXXXX)
python -B tools/build_release.py --output "$build_dir"
cd "$build_dir"
```

Alternatively, when a successful [Actions run](https://github.com/infantesromeroadrian/arca-dicta/actions/workflows/ci.yml)
is available for the intended commit, download its `source-installer-<commit SHA>`
artifact and unpack the GitHub artifact bundle. It contains the tarball,
checksum and manifest. These downloadable test artifacts are retained for
14 days; they are not a GitHub Release or an accepted alpha.

From the directory containing the tarball and checksum, verify and extract:

```sh
sha256sum -c arca-dicta-0.1.0-alpha.1.tar.gz.sha256
tar -xzf arca-dicta-0.1.0-alpha.1.tar.gz
cd arca-dicta-0.1.0-alpha.1
./install install --download-model
./install doctor --no-service
```

Installing directly from a mutable checkout without a generated release
manifest is refused. The checksum and manifest detect file corruption; they
are not an independent publisher signature. The [validation record](validation/ci.md)
identifies the earlier tested artifact. A new build, including documentation
changes, has its own manifest and checksum.

The default prefix is `${XDG_DATA_HOME:-$HOME/.local/share}/arca-dicta`.
`--prefix` precedes the subcommand and may contain spaces:

```sh
./install --prefix "$HOME/.local/share/ARCA Dicta" install --download-model
./install --prefix "$HOME/.local/share/ARCA Dicta" doctor --no-service
```

Preparation downloads Python wheels and approximately 484 MB of model weights;
allow additional disk space for two environments and the download cache. The
model is `Systran/faster-whisper-small` at revision
`536b0662742c02347bc0e980a01041f333bce120`. Four files are hash checked, including
`vocabulary.txt`. A real load of the three-marker subset fails with this
snapshot; the four-file set loads. Existing verified files may instead be
supplied with `install --model-dir /absolute/model/directory`. That external
directory is never removed by uninstall.

Preparation uses an empty owned prefix, copies only manifest-listed files,
checks imports and never starts a daemon, opens a microphone, uses the clipboard,
reads provider credentials or modifies desktop configuration. Repeating the
same intact installation is idempotent. Changed installed files cause a conflict;
this first alpha does not perform an in-place version upgrade. A preparation
failure removes its newly created runtime and partial downloads. The downloader
does not resume an unauthenticated partial file.

## Credentials and explicit activation

Groq credentials remain in hyprwhspr's private native credential store, under
`${XDG_DATA_HOME:-$HOME/.local/share}/hyprwhspr/credentials`. Provision the
`groq` entry through upstream's credential setup before activation. Do not put
an inline `rest_api_key` in config.json: activation refuses it to prevent the
upstream migration from changing the credential store during the transaction.
Existing credentials are not copied into the ARCA prefix.

The reviewer needs `OLLAMA_API_KEY` or `OLLAMA_CLOUD_API_KEY`. Existing service
environment references remain effective. For a fresh setup, create
`${XDG_CONFIG_HOME:-$HOME/.config}/arca-dicta/secrets.env` privately (directory
0700, file 0600) and edit an environment assignment there with your editor.
Never place the key in shell history, Git, an issue or a screenshot. The
generated drop-in reads this optional file; the installer does not read it.

When ready to replace the running native service:

```sh
./install activate
```

If a custom service or existing drop-in is reported, review its local baseline
then use `./install activate --adopt-existing`. This flag authorizes adoption
of that service context, not overwriting its files. Existing overrides are
backed up and retained. An override sorting after `zz-arca-dicta.conf` is refused.

Activation affects only **hyprwhspr.service** and a narrow native config patch:
Groq final STT, source-language autodetection, mandatory adjacent review hook,
HUD, full playback suppression, no unreviewed live typing, no Enter and no debug
recordings. Devices, existing bindings and unrelated fields remain. CLI commands
such as `hyprwhspr record toggle` keep using the same native IPC paths. No second
unit is started. Pending in-memory dictation is lost when the service stops;
finish or discard it first.

For a fresh config the application waits for external bindings. Add an available
Hyprland binding explicitly, after checking for conflicts, for example:

```ini
bind = SUPER, SPACE, exec, hyprwhspr record toggle
```

This is an example to review, not a file the installer edits. Existing bindings
are not replaced. Keep the extracted archive directory or its installed copy at
`<prefix>/releases/0.1.0-alpha.1/app/install` for later management; always supply
the same custom `--prefix` if one was used.

## Doctor, rollback and removal

`./install doctor` checks package contracts, owned-file integrity, model hashes,
imports and service metadata. `--no-service` omits systemd inspection. It never
contacts a provider or proves credential validity, microphone capture, visible
HUD, paste or audio restoration. A successful start still requires the
[physical scenarios](validation/scenarios.md) before acceptance.

```sh
./install deactivate
./install uninstall
```

Deactivation restores the previous service entrypoint and active/inactive state.
It restores original config bytes and mode when unchanged; if unrelated config
fields were edited, it restores only managed keys while retaining those edits.
A changed managed key or service definition is reported as a conflict before
stopping the working service. Resolve that difference against the private backup
and rerun; there is no force-overwrite option.

Backups and the durable activation journal live under the private prefix.
An ordinary activation failure triggers restoration. After an interrupted
activation/deactivation, rerun `deactivate`; if it reports a conflict, inspect
the saved baseline locally before editing. Never delete the journal to bypass
the active-installation check.

Uninstall refuses an active installation. It deletes only matching owned files,
preserves modified/unknown files and private backups, leaves native packages,
provider credentials, external model directories and global bindings alone.
It never recursively deletes a pre-existing user directory.

## Recorded validation and remaining acceptance

The local Arch validation passed 177 tests, Ruff, ShellCheck, 26 upstream
contracts and two identical builds. A fresh container user completed preparation,
synthetic local worker inference, repeat installation and uninstall. A separate
real activation passed doctor 35/35 and returned to the original service with
all 25 baseline files and modes restored and four sinks' mute/volume unchanged.
That activation check initiated no capture, inference or provider request.

These are results for the earlier frozen artifact documented in
[CI validation](validation/ci.md) and the [recovery record](validation/installer-baseline.md).
The existing personal runtime was accepted for personal use. Package validation
still needs physical dictation in an editor and browser, failure and cancellation
checks, audible playback restoration, latency measurements and a demo. The first
GitHub Release remains pending.
No physical latency or provider-quality claim follows from these checks.
