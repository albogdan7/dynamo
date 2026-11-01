# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Download pinned trace bytes for AISimulate's native Mooncake/Weka loaders."""

import argparse
import hashlib
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Source:
    url: str
    sha256: str
    lines: int | None = None


MOONCAKE = (
    "https://raw.githubusercontent.com/kvcache-ai/Mooncake/"
    "bd7526307049ee1612cfcb90daceffb128994ade/FAST25-release/traces"
)
SOURCES = {
    "mooncake-toolagent": Source(
        f"{MOONCAKE}/toolagent_trace.jsonl",
        "48a2db1a13d3bc05e6330140c64f604ba366df20d3c9e128b5c35a01c1fa5f71",
    ),
    "mooncake-conversation": Source(
        f"{MOONCAKE}/conversation_trace.jsonl",
        "b8cbb061a85206d729d91cdc2981f43c9e0d99209dce588d3af5f7934408b9df",
    ),
    "agentx": Source(
        "https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/"
        "resolve/23f152f6f0f9399a85901b89a6458def0ef16729/traces.jsonl",
        "26cc19dcee8ea765f93fa33062e2e5fe65aa86383c15730390839338ed762153",
        lines=16,
    ),
}


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(name: str, directory: Path) -> Path:
    """Cache a verified release (or complete-play prefix), without conversion."""
    source = SOURCES[name]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.jsonl"
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            staged = Path(temporary) / target.name
            with (
                urllib.request.urlopen(source.url, timeout=90) as response,
                staged.open("wb") as output,
            ):
                total = 0
                rows = 0
                while source.lines is None or rows < source.lines:
                    chunk = (
                        response.read(1024 * 1024)
                        if source.lines is None
                        else response.readline(64 * 1024 * 1024)
                    )
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 128 * 1024 * 1024:
                        raise ValueError("Trace download exceeds 128 MiB")
                    if source.lines is not None and not chunk.endswith(b"\n"):
                        raise ValueError("Incomplete AgentX play")
                    output.write(chunk)
                    rows += 1
            if checksum(staged) != source.sha256:
                raise ValueError(f"Downloaded {name} checksum mismatch")
            staged.replace(target)
    if checksum(target) != source.sha256:
        raise ValueError(f"Cached {name} checksum mismatch: {target}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=SOURCES)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).parent / "data"
    )
    args = parser.parse_args()
    print(fetch(args.source, args.output_dir))
