"""Full-release downloads and exact coverage ledgers; never sees a policy or score.

Every source request is accounted for exactly once, with a stable source ID and
its original file, line and in-line location. Requests the replay contract cannot
represent stay in the ledger with explicit flags. A trace with any blocked request
is not normalized or replayed, so a filtered run is never called a full trace.
"""

import gzip
import hashlib
import json
import math
import shutil
import subprocess
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .traces import ROOT, validate

RELEASES = json.loads(Path(__file__).with_name("releases.json").read_text())
BLOCK_TOKENS = 512
FLAGS = {
    "invalid_input": "Input count is missing, negative or not an integer.",
    "missing_output": "Output count is missing, negative or not an integer; lengths are never invented.",
    "zero_output": "Zero recorded output. The source does not establish that generation was truly empty (for example, zero completions on tool-call responses), so the count is ambiguous.",
    "infeasible": "ceil((input + output) / 512) exceeds the frozen cache capacity in blocks; capacity is never raised silently.",
    "input_exceeds_capacity": "The prompt alone exceeds capacity (a subset of infeasible; diagnostic only).",
    "nested_subagent": "Request in a nested subagent session; the FreeInference adapter has no spawn/join translation.",
    "overlap": "Request starts before its predecessor in the same session ends; its dispatch semantics are unresolved.",
    "subagent": "AgentX subagent request; the native importer preserves spawn/join (not a blocker).",
}
# Flags that the replay adapters for each source format cannot represent.
BLOCKING = {
    "mooncake": {"invalid_input", "missing_output", "zero_output", "infeasible"},
    "weka": {"invalid_input", "missing_output", "zero_output", "infeasible"},
    "freeinference": {
        "invalid_input",
        "missing_output",
        "zero_output",
        "infeasible",
        "nested_subagent",
        "overlap",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(trace: str) -> list[Path]:
    """Download missing files and verify every pinned byte count and checksum."""
    release = RELEASES[trace]
    paths = []
    for item in release["files"]:
        path = ROOT / ".cache/sources" / trace / item["path"]
        if not path.exists() or path.stat().st_size != item["bytes"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_name(path.name + ".partial")
            with (
                urllib.request.urlopen(
                    f"{release['base_url']}/{item['path']}", timeout=120
                ) as response,
                partial.open("wb") as output,
            ):
                shutil.copyfileobj(response, output, 1 << 20)
            partial.rename(path)
        if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise ValueError(f"Pinned checksum mismatch for {trace}/{item['path']}")
        paths.append(path)
    return paths


def classify(row: dict, kind: str, capacity_blocks: int) -> dict:
    flags = set(row.pop("flags", ()))
    tokens, output = row["input_tokens"], row["output_tokens"]
    if type(tokens) is not int or tokens < 0:
        flags.add("invalid_input")
    if type(output) is not int or output < 0:
        flags.add("missing_output")
    elif output == 0:
        flags.add("zero_output")
    if "invalid_input" not in flags:
        generated = output if type(output) is int and output > 0 else 0
        if math.ceil((tokens + generated) / BLOCK_TOKENS) > capacity_blocks:
            flags.add("infeasible")
        if math.ceil(tokens / BLOCK_TOKENS) > capacity_blocks:
            flags.add("input_exceeds_capacity")
    row["flags"] = sorted(flags)
    row["blocked_by"] = sorted(flags & BLOCKING[kind])
    return row


def mooncake_requests(path: Path, name: str):
    with path.open() as handle:
        for number, line in enumerate(handle, 1):
            row = json.loads(line)
            yield {
                "source_id": f"row:{number - 1}",
                "file": name,
                "line": number,
                "location": "$",
                "session_id": None,
                "day": None,
                "model": None,
                "input_tokens": row["input_length"],
                "output_tokens": row["output_length"],
            }


def agentx_requests(path: Path, name: str):
    """Main-agent turns and subagent inner requests, in source order."""
    with path.open() as handle:
        for number, line in enumerate(handle, 1):
            play = json.loads(line)
            for index, entry in enumerate(play["requests"]):
                nested = entry["type"] == "subagent"
                for inner, request in enumerate(
                    entry["requests"] if nested else [entry]
                ):
                    yield {
                        "source_id": f"{play['id']}:{index}"
                        + (f".{inner}" if nested else ""),
                        "file": name,
                        "line": number,
                        "location": f"requests[{index}]"
                        + (f".requests[{inner}]" if nested else ""),
                        "session_id": play["id"]
                        + (f"/subagent[{index}]" if nested else ""),
                        "day": None,
                        "model": request.get("model"),
                        "input_tokens": request.get("in"),
                        "output_tokens": request.get("out"),
                        "flags": ["subagent"] if nested else [],
                    }


def free_session(session: dict, name: str, number: int, location: str, day, depth=0):
    """Requests in chronological order, recursing into nested subagent sessions."""
    previous = None
    for index, request in enumerate(session["requests"]):
        here = f"{location}.requests[{index}]"
        flags = ["nested_subagent"] if depth else []
        if previous is not None and request["start_ms"] < previous["end_ms"]:
            flags.append("overlap")
        previous = request
        roles = request.get("role_tokens") or {}
        valid = roles and all(type(v) is int and v >= 0 for v in roles.values())
        yield {
            "source_id": f"{session['session_id']}:{index}",
            "file": name,
            "line": number,
            "location": here,
            "session_id": session["session_id"],
            "day": day,
            "model": f"{request.get('model')}:{request.get('provider')}",
            "input_tokens": sum(roles.values()) if valid else None,
            "output_tokens": (request.get("provider_tokens") or {}).get("completion"),
            "tool_calls": len(request.get("tool_calls") or []),
            "flags": flags,
        }
        for call_index, call in enumerate(request.get("tool_calls") or []):
            if call.get("subagent"):
                yield from free_session(
                    call["subagent"],
                    name,
                    number,
                    f"{here}.tool_calls[{call_index}].subagent",
                    day,
                    depth + 1,
                )


def free_file(path: Path, name: str):
    """Stream one zstd day file, hashing the decompressed bytes as they are parsed."""
    digest, lines = hashlib.sha256(), 0
    process = subprocess.Popen(["zstd", "-dc", str(path)], stdout=subprocess.PIPE)
    day = Path(name).name.split(".")[0]
    for number, line in enumerate(process.stdout, 1):
        digest.update(line)
        lines = number
        yield from free_session(json.loads(line), name, number, "$", day)
    if process.wait() != 0:
        raise RuntimeError(f"zstd failed for {name}")
    yield {"decompressed": {"sha256": digest.hexdigest(), "lines": lines}}


class Tally:
    """Coverage counts by flag, blocker, model, day, file and session."""

    def __init__(self):
        self.flags, self.blockers, self.totals = Counter(), Counter(), Counter()
        self.models = defaultdict(Counter)
        self.days = defaultdict(Counter)
        self.files = defaultdict(Counter)
        self.sessions = defaultdict(Counter)
        self.nested_sessions = set()
        self.ids = set()

    def add(self, row: dict) -> None:
        if row["source_id"] in self.ids:
            raise ValueError(f"Duplicate source request identity: {row['source_id']}")
        self.ids.add(row["source_id"])
        if {"nested_subagent", "subagent"} & set(row["flags"]):
            self.nested_sessions.add(row["session_id"])
        else:
            self.totals["root_tool_calls"] += row.get("tool_calls", 0)
        blocked = int(bool(row["blocked_by"]))
        self.totals["tool_calls"] += row.get("tool_calls", 0)
        # Requests that a larger cache alone would not unblock.
        self.totals["blocked_excluding_capacity"] += bool(
            set(row["blocked_by"]) - {"infeasible"}
        )
        if type(row["input_tokens"]) is int:
            generated = row["output_tokens"] if type(row["output_tokens"]) is int else 0
            self.totals["max_context_blocks"] = max(
                self.totals["max_context_blocks"],
                math.ceil((row["input_tokens"] + max(generated, 0)) / BLOCK_TOKENS),
            )
        self.flags.update(row["flags"])
        self.blockers.update(row["blocked_by"])
        for table, key in (
            (self.models, row["model"] or "unrecorded"),
            (self.days, row["day"] or "unrecorded"),
            (self.files, row["file"]),
            (self.sessions, row["session_id"] or "unrecorded"),
        ):
            table[key]["requests"] += 1
            table[key]["blocked"] += blocked

    def merge(self, other: "Tally") -> None:
        if self.ids & other.ids:
            raise ValueError("Duplicate source request identity across files")
        self.ids |= other.ids
        self.nested_sessions |= other.nested_sessions
        self.flags.update(other.flags)
        self.blockers.update(other.blockers)
        largest = max(
            self.totals["max_context_blocks"], other.totals["max_context_blocks"]
        )
        self.totals.update(other.totals)
        self.totals["max_context_blocks"] = largest
        for name in ("models", "days", "files", "sessions"):
            for key, counts in getattr(other, name).items():
                getattr(self, name)[key].update(counts)


def ledger_part(arguments):
    """Classify one source file into a gzip ledger part; returns its tally."""
    kind, path, name, capacity_blocks, part = arguments
    walk = {
        "mooncake": mooncake_requests,
        "weka": agentx_requests,
        "freeinference": free_file,
    }[kind]
    tally, decompressed = Tally(), None
    with gzip.open(part, "wt") as ledger:
        for row in walk(Path(path), name):
            if "decompressed" in row:
                decompressed = row["decompressed"]
                continue
            row = classify(row, kind, capacity_blocks)
            tally.add(row)
            ledger.write(json.dumps(row, separators=(",", ":")) + "\n")
    return name, tally, decompressed


def publisher_entries(trace: str) -> dict:
    """Decompressed-file checksums and line counts from weekly publisher manifests."""
    expected = {}
    for item in RELEASES[trace]["files"]:
        if item["role"] == "manifest":
            week = Path(item["path"]).parent
            manifest = json.loads(
                (ROOT / ".cache/sources" / trace / item["path"]).read_text()
            )
            for relative, value in manifest["files"].items():
                expected[str(week / relative) + ".zst"] = value
    return expected


def publisher_checks(decompressed: dict, expected: dict) -> dict:
    """Every decompressed day must match its publisher's checksum and line count."""
    if not decompressed:
        return {}
    if set(decompressed) != set(expected):
        raise ValueError("Downloaded day files differ from the publisher manifests")
    for name, actual in decompressed.items():
        published = expected.get(name)
        if published is None or (published["sha256"], published["lines"]) != (
            actual["sha256"],
            actual["lines"],
        ):
            raise ValueError(
                f"Decompressed {name} disagrees with its publisher manifest"
            )
    return {"decompressed_files_verified": len(decompressed)}


def normalize(trace: str, paths: list[Path], directory: Path) -> list[dict]:
    """Write the immutable replay inputs for a fully covered trace."""
    release = RELEASES[trace]
    if release["kind"] != "mooncake":
        raise ValueError(
            f"No full-release normalizer for {release['kind']}; only an unblocked ledger may be normalized"
        )
    (source,) = paths
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    validate(rows, release["source_block_size"])
    recording = directory / "recordings" / "full.jsonl"
    recording.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, recording)
    return [
        {
            "id": "full",
            "path": str(recording.relative_to(ROOT)),
            "sha256": sha256(recording),
            "requests": len(rows),
            "first_arrival_ms": rows[0]["timestamp"],
            "last_arrival_ms": rows[-1]["timestamp"],
            "format": "mooncake",
            "source_block_size": release["source_block_size"],
            "timing": "original fixed arrivals; cold start; cache retained across the whole recording",
        }
    ]


def build(trace: str, cache_tokens: int, directory: Path, workers: int = 8) -> dict:
    """Ledger every source request, then normalize only an unblocked trace."""
    if cache_tokens % BLOCK_TOKENS:
        raise ValueError("Cache capacity must be whole 512-token blocks")
    release = RELEASES[trace]
    paths = fetch(trace)
    if directory.exists():
        shutil.rmtree(directory)
    (directory / "ledger-parts").mkdir(parents=True)
    jobs = [
        (
            release["kind"],
            str(path),
            item["path"],
            cache_tokens // BLOCK_TOKENS,
            str(directory / "ledger-parts" / f"{index:04}.jsonl.gz"),
        )
        for index, (item, path) in enumerate(zip(release["files"], paths, strict=True))
        if item["role"] == "trace"
    ]
    tally, decompressed = Tally(), {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for name, part, unpacked in pool.map(ledger_part, jobs):
            tally.merge(part)
            if unpacked:
                decompressed[name] = unpacked
    # Concatenated gzip members form one valid stream in source-file order.
    ledger = directory / "ledger.jsonl.gz"
    with ledger.open("wb") as output:
        for job in jobs:
            with Path(job[4]).open("rb") as part:
                shutil.copyfileobj(part, output)
    shutil.rmtree(directory / "ledger-parts")
    requests = sum(counts["requests"] for counts in tally.files.values())
    blocked = sum(counts["blocked"] for counts in tally.files.values())
    observed = {"requests": requests}
    if release["kind"] != "mooncake":
        nested = tally.flags["nested_subagent"] + tally.flags["subagent"]
        observed.update(
            root_sessions=len(tally.sessions) - len(tally.nested_sessions),
            nested_sessions=len(tally.nested_sessions),
            root_requests=requests - nested,
            nested_requests=nested,
        )
    if release["kind"] == "freeinference":
        observed["tool_calls"] = tally.totals["tool_calls"]
        observed["root_tool_calls"] = tally.totals["root_tool_calls"]
    if release["kind"] == "weka":
        observed["plays"] = observed["root_sessions"]
        observed["main_turns"] = observed["root_requests"]
        observed["subagent_groups"] = observed["nested_sessions"]
        observed["subagent_inner_requests"] = observed["nested_requests"]
    # Publisher totals are compared, never forced to agree.
    reconciliation = {
        key: {"published": value, "observed": observed.get(key)}
        for key, value in release["published"].items()
    }
    result = {
        "trace": trace,
        "kind": release["kind"],
        "revision": release["revision"],
        "license": release["license"],
        "files": release["files"],
        "cache_tokens": cache_tokens,
        "capacity_blocks": cache_tokens // BLOCK_TOKENS,
        "block_tokens": BLOCK_TOKENS,
        "observed": observed,
        "publisher_count_checks": reconciliation,
        "publisher_counts_match": all(
            item["published"] == item["observed"] for item in reconciliation.values()
        ),
        "replayable_requests": requests - blocked,
        "blocked_requests": blocked,
        "blocked_excluding_capacity": tally.totals["blocked_excluding_capacity"],
        "cache_tokens_to_fit_every_request": tally.totals["max_context_blocks"]
        * BLOCK_TOKENS,
        "flag_counts": dict(sorted(tally.flags.items())),
        "blocker_counts": dict(sorted(tally.blockers.items())),
        "flag_definitions": {flag: FLAGS[flag] for flag in sorted(tally.flags)},
        "by_model": {k: dict(v) for k, v in sorted(tally.models.items())},
        "by_day": {k: dict(v) for k, v in sorted(tally.days.items())},
        "by_file": {k: dict(v) for k, v in sorted(tally.files.items())},
        "sessions_with_blocked_requests": sum(
            1 for counts in tally.sessions.values() if counts["blocked"]
        ),
        "ledger": {
            "path": str(ledger.relative_to(ROOT)),
            "sha256": sha256(ledger),
            "rows": requests,
        },
        **publisher_checks(
            decompressed, publisher_entries(trace) if decompressed else {}
        ),
    }
    (directory / "sessions.json").write_text(
        json.dumps({k: dict(v) for k, v in sorted(tally.sessions.items())}) + "\n"
    )
    result["status"] = "blocked" if blocked else "ready"
    result["recordings"] = [] if blocked else normalize(trace, paths, directory)
    return result
