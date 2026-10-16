"""Fetch pinned upstream sources and verify the 26 native compatibility entries."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import urllib.request


UPSTREAM_URL = "https://github.com/goodroot/hyprwhspr/archive/refs/tags/v1.49.0.tar.gz"
UPSTREAM_SHA256 = "b1e552bb3be43438d3080a4dc1c6d550acb8860582f45f8ff353b35d29b5263c"
UPSTREAM_COMMIT = "88ef652a974854fefdd1ef2bfc9c79ae31c52008"
UPSTREAM_PREFIX = "hyprwhspr-1.49.0/"


def prepare_upstream(source: Path, destination: Path, proof: Path) -> None:
    if destination.exists():
        raise ValueError("upstream destination must not already exist")
    with urllib.request.urlopen(UPSTREAM_URL, timeout=60) as response:
        payload = response.read(32 * 1024 * 1024 + 1)
    if hashlib.sha256(payload).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("upstream download failed its pinned SHA-256 check")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if (not member.name.startswith(UPSTREAM_PREFIX)
                    and member.name != UPSTREAM_PREFIX.rstrip("/")):
                raise ValueError("upstream archive has an unexpected root")
            if (".." in parts or not (member.isfile() or member.isdir())
                    or member.size > 16 * 1024 * 1024):
                raise ValueError("upstream archive has an unsafe member")
        destination.mkdir(parents=True)
        archive.extractall(destination, filter="data")
    library = destination / UPSTREAM_PREFIX / "lib"
    checks = []
    for module in ("main.py", "hud_bridge.py"):
        tree = ast.parse((source / module).read_text(encoding="utf-8"))
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "SUPPORTED_FILES"
                               for target in node.targets)]
        if len(assignments) != 1:
            raise ValueError("native contract declaration is missing or ambiguous")
        files = ast.literal_eval(assignments[0].value)
        for name, expected in files.items():
            actual = hashlib.sha256((library / name).read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"native contract mismatch: {module}: {name}")
            checks.append({"module": module, "path": name, "sha256": actual})
    if len(checks) != 26:
        raise ValueError("native contract count changed; review the CI baseline")
    proof.write_text(json.dumps({
        "url": UPSTREAM_URL,
        "source_sha256": UPSTREAM_SHA256,
        "source_commit": UPSTREAM_COMMIT,
        "contract_entries": checks,
        "scope": "Source contract only; not an installed Arch package identity.",
    }, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(library)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    args = parser.parse_args(argv)
    prepare_upstream(args.source, args.destination, args.proof)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
