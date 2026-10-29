"""Download, verify, and patch the exact AISimulate core pinned by Dynamo."""

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

VERSION = "0.13.0-dev.202610160000000073"
SHA256 = "ede3ff2e2fd29e1c53dc1656ef5e43d4c77c0841483fe37685d3d517566016b2"
ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use an already downloaded .crate")
    args = parser.parse_args()
    patch = ROOT / "custom_eviction.patch"
    identity = SHA256 + ":" + hashlib.sha256(patch.read_bytes()).hexdigest()
    destination = ROOT / ".deps" / "aisimulate-core"
    marker = destination / ".kv-patch-id"
    if destination.exists():
        if marker.exists() and marker.read_text() == identity:
            print(f"Already prepared: {destination}")
            return
        parser.error(f"{destination} already exists with different contents; move it aside first")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        staging = Path(staging)
        archive = staging / "core.crate"
        if args.archive:
            shutil.copyfile(args.archive, archive)
        else:
            url = f"https://static.crates.io/crates/aisimulate-core/aisimulate-core-{VERSION}.crate"
            with urllib.request.urlopen(url, timeout=60) as response:
                archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise ValueError("AISimulate archive checksum mismatch")
        with tarfile.open(archive, "r:gz") as bundle:
            # The checksum pins the archive; also reject paths/links escaping staging.
            for member in bundle.getmembers():
                target = (staging / member.name).resolve()
                if staging.resolve() not in target.parents or not (member.isfile() or member.isdir()):
                    raise ValueError(f"Unsafe archive member: {member.name}")
            bundle.extractall(staging)
        source = staging / f"aisimulate-core-{VERSION}"
        subprocess.run(["patch", "--batch", "-p1", "-i", str(patch)], cwd=source, check=True)
        (source / ".kv-patch-id").write_text(identity)
        source.rename(destination)
    print(f"Prepared {destination}")


if __name__ == "__main__":
    main()

