#!/bin/bash
# Run only in the disposable Arch container; never bootstrap the developer host.
set -euo pipefail
if [[ ! -f /.dockerenv || $(id -u) != 0 || $# != 1 || $1 != /out ]]; then
    printf '%s\n' 'Use the documented disposable Docker command with output /out.' >&2
    exit 64
fi
pacman -Syu --noconfirm --needed \
    python python-gobject python-cairo python-evdev portaudio libsndfile libpulse \
    gtk4 gtk4-layer-shell pango ttf-dejavu uv ruff shellcheck ca-certificates
mkdir -p /out
pacman -Q > /out/arch-packages.txt
python --version > /out/python-version.txt
uv --version > /out/uv-version.txt
ruff --version > /out/ruff-version.txt
shellcheck --version > /out/shellcheck-version.txt
useradd --create-home --shell /bin/bash dicta-ci
chown -R dicta-ci:dicta-ci /out
exec runuser -u dicta-ci -- bash /src/tools/ci/check.sh /out
