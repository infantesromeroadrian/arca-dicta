#!/bin/bash
set -euo pipefail
if [[ $# != 1 || $1 != /out || $(id -u) == 0 ]]; then
    printf '%s\n' 'CI checks require the unprivileged container user and /out.' >&2
    exit 64
fi
output=$1
work_dir=$(mktemp -d /tmp/arca-dicta-ci.XXXXXXXX)
trap 'rm -rf -- "$work_dir"' EXIT
export PYTHONDONTWRITEBYTECODE=1
export XDG_RUNTIME_DIR="$work_dir/runtime"
mkdir -m 700 "$XDG_RUNTIME_DIR"
cd /src
uv venv --python /usr/bin/python3 --system-site-packages "$work_dir/test-venv"
uv pip install --python "$work_dir/test-venv/bin/python" --require-hashes --no-build \
    --requirements requirements/daemon.lock 2>&1 | tee "$output/test-dependencies.txt"
test_python="$work_dir/test-venv/bin/python"
python -B tools/ci/prepare_upstream.py \
    --destination "$work_dir/upstream" --proof "$output/upstream-contract.json"
export ARCA_DICTA_UPSTREAM_LIB="$work_dir/upstream/hyprwhspr-1.49.0/lib"
"$test_python" -B -m unittest discover -v 2>&1 | tee "$output/unittest.txt"
ruff check --no-cache . 2>&1 | tee "$output/ruff.txt"
shellcheck install launch review-hook tools/ci/run.sh tools/ci/check.sh tools/ci/clean_install.sh 2>&1 | tee "$output/shellcheck.txt"
python -B tools/build_release.py --output "$output/release" | tee "$output/build.txt"
python -B tools/build_release.py --output "$work_dir/repeated" >> "$output/build.txt"
python - "$output"/release/*.tar.gz "$work_dir"/repeated/*.tar.gz <<'PY'
from pathlib import Path
import sys
if Path(sys.argv[1]).read_bytes() != Path(sys.argv[2]).read_bytes():
    raise SystemExit("Repeated release archives differ")
PY
(cd "$output/release" && sha256sum --check ./*.sha256)
printf '%s\n' 'PASS: repeated source archive has identical bytes.' >> "$output/build.txt"
