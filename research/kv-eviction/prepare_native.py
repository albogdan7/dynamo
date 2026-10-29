#!/usr/bin/env python3
"""Build the research-only adapter; never modify the workspace's simulator pin."""

import hashlib
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "0.13.0-dev.202610160000000073"
SHA256 = "ede3ff2e2fd29e1c53dc1656ef5e43d4c77c0841483fe37685d3d517566016b2"


def main():
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    archive = cache / "aisimulate-core.crate"
    if not archive.exists():
        print("Downloading pinned AISimulate core", flush=True)
        with urllib.request.urlopen(
            f"https://crates.io/api/v1/crates/aisimulate-core/{VERSION}/download",
            timeout=60,
        ) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError("Simulator archive checksum mismatch; remove it and retry")
    patched = cache / "patched-core"
    if patched.exists():
        shutil.rmtree(patched)
    with tarfile.open(archive) as source:
        source.extractall(cache, filter="data")
    shutil.copytree(cache / f"aisimulate-core-{VERSION}", patched)
    subprocess.run(
        ["patch", "-p1", "-i", str(ROOT / "native" / "core.patch")],
        cwd=patched,
        check=True,
    )
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--locked",
            "--manifest-path",
            str(ROOT / "native/Cargo.toml"),
        ],
        check=True,
    )
    print(ROOT / "native/target/release/kv-eviction-replay")


if __name__ == "__main__":
    main()
