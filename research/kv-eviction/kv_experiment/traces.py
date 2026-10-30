"""Pinned trace manifests and windows; native replay owns Mooncake lowering."""

import hashlib
import json
import math
import shutil
import subprocess
import urllib.request
from pathlib import Path

from .sources import SOURCES

ROOT = Path(__file__).resolve().parents[1]
TRACE = SOURCES["mooncake-toolagent"].metadata()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(rows: list[dict], block_size: int = 512) -> None:
    previous = 0.0
    parents = {}
    for row in rows:
        timestamp = row["timestamp"]
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
            or timestamp < previous
        ):
            raise ValueError("Trace arrivals must be finite, nonnegative and ordered")
        previous = timestamp
        for key in ("input_length", "output_length"):
            if type(row[key]) is not int or row[key] < 0:
                raise ValueError(f"{key} must be a nonnegative integer")
        if len(row["hash_ids"]) != math.ceil(row["input_length"] / block_size):
            raise ValueError("Trace hash count does not match input length")
        parent = None
        for block_hash in row["hash_ids"]:
            if type(block_hash) is not int or not 0 <= block_hash < 2**64:
                raise ValueError("Invalid prefix hash")
            if block_hash in parents and parents[block_hash] != parent:
                raise ValueError("Prefix identity has inconsistent ancestors")
            parents[block_hash] = parent
            parent = block_hash


def fetch(name: str = "mooncake-toolagent") -> Path:
    source = SOURCES[name]
    path = ROOT / ".cache" / source.filename
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(path.suffix + ".partial")
        with (
            urllib.request.urlopen(source.url, timeout=90) as response,
            temporary.open("wb") as output,
        ):
            if source.subset_lines is None:
                shutil.copyfileobj(response, output)
            else:
                downloaded = 0
                for _ in range(source.subset_lines):
                    line = response.readline(64 * 1024 * 1024)
                    downloaded += len(line)
                    if not line.endswith(b"\n") or downloaded > 128 * 1024 * 1024:
                        raise ValueError(
                            "Complete-session subset exceeds download bounds"
                        )
                    output.write(line)
        if digest(temporary) != source.sha256:
            raise ValueError("Downloaded trace checksum mismatch")
        temporary.rename(path)
    if digest(path) != source.sha256:
        raise ValueError("Cached trace checksum mismatch")
    if source.uncompressed_sha256:
        raw = path.with_suffix("")
        if not raw.exists():
            if shutil.which("zstd") is None:
                raise RuntimeError("FreeInference download requires the zstd command")
            subprocess.run(
                ["zstd", "-d", "-q", "-f", str(path), "-o", str(raw)], check=True
            )
        if digest(raw) != source.uncompressed_sha256:
            raise ValueError("Decompressed trace checksum mismatch")
        return raw
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if source.subset_lines is not None and len(rows) != source.subset_lines:
        raise ValueError("Complete-session subset count mismatch")
    if (
        source.kind == "mooncake"
        and source.requests is not None
        and len(rows) != source.requests
    ):
        raise ValueError("Trace request count mismatch")
    if source.kind == "mooncake":
        validate(rows)
    return path


def windows(
    source: Path,
    output: Path,
    count: int,
    arrival_scale: float = 1,
    name: str = "mooncake-toolagent",
) -> dict:
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    midpoint = len(rows) // 2
    if not 1 <= count <= midpoint:
        raise ValueError(f"window-requests must be between 1 and {midpoint}")
    manifest = SOURCES[name].metadata()
    manifest["format"] = "mooncake"
    manifest["execution"] = "fixed arrivals, cold state per window"
    manifest["arrival_time_multiplier"] = arrival_scale
    manifest["windows"] = {}
    for split, start in (("tuning", 0), ("evaluation", midpoint)):
        path = output / f"{split}-trace.jsonl"
        selected = rows[start : start + count]
        origin = selected[0]["timestamp"]
        selected = [
            dict(row, timestamp=origin + (row["timestamp"] - origin) * arrival_scale)
            for row in selected
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in selected))
        manifest["windows"][split] = {
            "start_row": start,
            "requests": count,
            "first_arrival_ms": selected[0]["timestamp"],
            "last_arrival_ms": selected[-1]["timestamp"],
            "sha256": digest(path),
            "cold_start": True,
        }
    return manifest
