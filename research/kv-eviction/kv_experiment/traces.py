"""Pinned trace manifests and windows; native replay owns Mooncake lowering."""

import hashlib
import json
import math
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "bd7526307049ee1612cfcb90daceffb128994ade"
TRACE = {
    "name": "mooncake-toolagent",
    "requests": 23608,
    "block_size": 512,
    "revision": REVISION,
    "license": "Apache-2.0 (repository LICENSE-APACHE)",
    "license_url": f"https://github.com/kvcache-ai/Mooncake/blob/{REVISION}/LICENSE-APACHE",
    "url": f"https://raw.githubusercontent.com/kvcache-ai/Mooncake/{REVISION}/FAST25-release/traces/toolagent_trace.jsonl",
    "sha256": "48a2db1a13d3bc05e6330140c64f604ba366df20d3c9e128b5c35a01c1fa5f71",
    "arrival_unit": "milliseconds",
    "hashes": "ordered remapped prefix block identities",
}


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


def fetch() -> Path:
    path = ROOT / ".cache/mooncake-toolagent.jsonl"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        with urllib.request.urlopen(TRACE["url"], timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != TRACE["sha256"]:
            raise ValueError("Downloaded trace checksum mismatch")
        path.write_bytes(data)
    if digest(path) != TRACE["sha256"]:
        raise ValueError("Cached trace checksum mismatch")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if len(rows) != TRACE["requests"]:
        raise ValueError("Trace request count mismatch")
    validate(rows)
    return path


def windows(source: Path, output: Path, count: int, arrival_scale: float = 1) -> dict:
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    midpoint = len(rows) // 2
    if not 1 <= count <= midpoint:
        raise ValueError(f"window-requests must be between 1 and {midpoint}")
    manifest = dict(TRACE)
    manifest["arrival_time_multiplier"] = arrival_scale
    manifest["windows"] = {}
    for name, start in (("tuning", 0), ("evaluation", midpoint)):
        path = output / f"{name}-trace.jsonl"
        selected = rows[start : start + count]
        origin = selected[0]["timestamp"]
        selected = [
            dict(row, timestamp=origin + (row["timestamp"] - origin) * arrival_scale)
            for row in selected
        ]
        path.write_text("".join(json.dumps(row) + "\n" for row in selected))
        manifest["windows"][name] = {
            "start_row": start,
            "requests": count,
            "first_arrival_ms": selected[0]["timestamp"],
            "last_arrival_ms": selected[-1]["timestamp"],
            "sha256": digest(path),
            "cold_start": True,
        }
    return manifest
