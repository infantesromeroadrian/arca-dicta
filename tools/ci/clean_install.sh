#!/bin/bash
# Opt-in integration test: installs packages only inside an ephemeral container.
set -euo pipefail
if [[ ! -f /.dockerenv || $(id -u) != 0 || $# -lt 1 ]]; then
    printf '%s\n' 'Run inside the documented disposable Arch container as root.' >&2
    exit 64
fi
mode=$1
if [[ $mode != prepare && $mode != exercise && $mode != all ]]; then
    exit 64
fi
if [[ $mode != prepare && $# != 2 ]]; then
    printf '%s\n' 'Pass the final source archive mounted under /artifact/.' >&2
    exit 64
fi
export PYTHONDONTWRITEBYTECODE=1

fetch_recipe() {
    local package=$1 commit=$2 filename=$3 expected=$4 directory=$5
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
        "https://aur.archlinux.org/cgit/aur.git/plain/$filename?h=$package&id=$commit" \
        --output "$directory/$filename"
    printf '%s  %s\n' "$expected" "$directory/$filename" | sha256sum --check
    printf '%s\t%s\t%s\t%s\n' "$package" "$commit" "$filename" "$expected" >> /out/aur-recipes.tsv
}

build_package() {
    local package=$1 commit=$2 expected=$3
    local directory="/tmp/arca-dicta-aur/$package"
    mkdir -p "$directory"
    fetch_recipe "$package" "$commit" PKGBUILD "$expected" "$directory"
    if [[ $package == hyprwhspr ]]; then
        fetch_recipe "$package" "$commit" hyprwhspr.install \
            85e2788769b75b55c6d0f6df6916f792d65c7be428b20bb946ff95905a016e88 "$directory"
    fi
    chown -R dicta-build:dicta-build "$directory"
    (
        cd "$directory"
        runuser -u dicta-build -- makepkg --noconfirm
        pacman -U --noconfirm ./*.pkg.tar.zst
        cp ./*.pkg.tar.zst /out/prerequisite-packages/
    )
}

if [[ $mode == prepare || $mode == all ]]; then
    pacman -Syu --noconfirm --needed \
        base-devel curl ca-certificates python python-build python-installer \
        python-setuptools python-wheel python-cffi python-numpy python-soxr \
        python-evdev python-pyperclip python-pyudev python-rich python-jsonschema \
        python-gobject python-cairo gtk4 gtk4-layer-shell pango portaudio \
        libsndfile libpulse uv wtype ydotool wl-clipboard xclip xdotool xorg-xprop \
        pipewire pipewire-alsa pipewire-pulse espeak-ng
    useradd --create-home --shell /bin/bash dicta-build
    useradd --create-home --shell /bin/bash dicta-clean
    mkdir -p /tmp/arca-dicta-aur /out/prerequisite-packages /out/clean-user
    mkdir -p /usr/share/doc # The minimal image excludes docs, but pacman owns this parent.
    chmod 755 /out
    chown dicta-clean:dicta-clean /out/clean-user
    build_package python-pulsectl 1f8e91936a5b2169da91a7de71c8be1c774f11fc \
        f595ae3645a7d2f02accb6bda329ffefdfaf444bb63b593d24c154cec170739f
    build_package python-sounddevice 117b08ef8841cc40b9285d95fc59db9a3216d049 \
        bd070c3f5dae3bd5914df45e907a73ac5fb99c746939bf599b2a5de43b6fe28b
    build_package hyprwhspr 7ea4a07c09656c4c84e6cecc9636e969b3fbe553 \
        0b1b6c336c5d6c2d20e60f5d49ebc49ac069ff2c8038351745a976884f363f99
    pacman -Q > /out/clean-arch-packages.txt
    (
        cd /out/prerequisite-packages
        sha256sum ./*.pkg.tar.zst > ../prerequisite-packages.sha256
    )
    python --version > /out/clean-python-version.txt
    uv --version > /out/clean-uv-version.txt
fi
if [[ $mode == exercise || $mode == all ]]; then
    case "$2" in
        /artifact/*.tar.gz) ;;
        *) printf '%s\n' 'Artifact must be mounted read-only under /artifact/.' >&2; exit 64 ;;
    esac
    artifact_copy=$(mktemp -d /tmp/arca-dicta-source.XXXXXXXX)
    trap 'rm -rf -- "$artifact_copy"' EXIT
    cp -- "$2" "$artifact_copy/release.tar.gz"
    chown -R dicta-clean:dicta-clean "$artifact_copy"
    runuser -u dicta-clean -- python -B /src/tools/ci/clean_install.py \
        --artifact "$artifact_copy/release.tar.gz" --output /out/clean-user
    pacman -Qkk hyprwhspr > /out/native-package-integrity.txt
fi
