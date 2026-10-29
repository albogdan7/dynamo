"""Complete-session adapters to the existing agentic-Mooncake v2 contract."""

import itertools
import json
import math
import subprocess
from pathlib import Path

from .sources import SOURCES
from .traces import ROOT, digest


def weka_requests(session: dict) -> list[dict]:
    return [
        request
        for entry in session["requests"]
        for request in (entry["requests"] if entry["type"] == "subagent" else [entry])
    ]


def qualify_agentx(session: dict) -> list[str]:
    requests = weka_requests(session)
    reasons = []
    if not requests or any(
        type(row["out"]) is not int or row["out"] <= 0 for row in requests
    ):
        reasons.append("nonpositive or missing output count")
    if len(requests) > 128:
        reasons.append("more than 128 model requests in complete play")
    if any(row["in"] + row["out"] > 196608 for row in requests):
        reasons.append("context exceeds bounded 196608-token workload")
    if not any(entry["type"] == "subagent" for entry in session["requests"]):
        reasons.append("no subagent group")
    return reasons


def qualify_free(session: dict) -> list[str]:
    requests = session["requests"]
    reasons = []
    if not requests or any(
        type(row["provider_tokens"]["completion"]) is not int
        or row["provider_tokens"]["completion"] <= 0
        for row in requests
    ):
        reasons.append("nonpositive or missing reported completion count")
    if any(tool["subagent"] for row in requests for tool in row["tool_calls"]):
        reasons.append("nested sessions require unsupported join semantics")
    if any(
        right["start_ms"] < left["end_ms"]
        for left, right in itertools.pairwise(requests)
    ):
        reasons.append("overlapping requests in a sequential stream")
    for row in requests:
        counts = row["role_tokens"].values()
        if any(type(value) is not int or value < 0 for value in counts):
            reasons.append("invalid tokenizer counts")
            break
        tokens = sum(row["role_tokens"].values())
        if not tokens > 0 or not 0 <= tokens - len(row["block_ids"]) * 16 < 16:
            reasons.append("tokenizer count does not match complete 16-token blocks")
            break
        if row["end_ms"] < row["start_ms"]:
            reasons.append("negative recorded service duration")
            break
    return reasons


def free_rows(sessions: list[dict]) -> list[dict]:
    """Preserve observed think/tool gaps; never reuse unknown partial tails."""
    if any(qualify_free(session) for session in sessions):
        raise ValueError("FreeInference adapter requires qualified complete sessions")
    origin = min(session["requests"][0]["start_ms"] for session in sessions)
    namespace = {}
    rows = []
    for ordinal, session in enumerate(sessions):
        previous = None
        for index, request in enumerate(session["requests"]):
            identity = f"{session['session_id']}:{index}"
            model = f"{request['model']}:{request['provider']}"
            tokens = sum(request["role_tokens"].values())
            hashes = []
            for block in request["block_ids"]:
                if type(block) is not int or not 0 <= block < 2**64:
                    raise ValueError("Invalid FreeInference block identity")
                hashes.append(namespace.setdefault((model, block), len(namespace)))
            if tokens % 16:
                hashes.append(
                    namespace.setdefault(
                        (model, f"private-tail:{identity}"), len(namespace)
                    )
                )
            dependencies = []
            if previous is not None:
                dependencies.append(
                    {
                        "request_id": f"{session['session_id']}:{index - 1}",
                        "trigger": "completion",
                        "relation": "sequence",
                        "delay_ms": request["start_ms"] - previous["end_ms"],
                    }
                )
            rows.append(
                {
                    "request_id": identity,
                    "play_id": session["session_id"],
                    "source_play_ordinal": ordinal,
                    "session_id": session["session_id"],
                    "model": model,
                    "input_length": tokens,
                    "output_length": request["provider_tokens"]["completion"],
                    "hash_ids": hashes,
                    "dependencies": dependencies,
                    "not_before_ms": request["start_ms"] - origin
                    if previous is None
                    else 0,
                    "recorded_api_time_ms": request["end_ms"] - request["start_ms"],
                }
            )
            previous = request
    return rows


def write_agentic(
    path: Path, header: dict, rows: list[dict], arrival_scale: float
) -> None:
    with path.open("w") as output:
        output.write(json.dumps(header) + "\n")
        for row in rows:
            row["not_before_ms"] *= arrival_scale
            for edge in row.get("dependencies", []):
                edge["delay_ms"] *= arrival_scale
            output.write(json.dumps(row) + "\n")


def session_windows(
    name: str, source: Path, output: Path, count: int, arrival_scale: float
) -> dict:
    specification = SOURCES[name]
    sessions = [json.loads(line) for line in source.read_text().splitlines()]
    agentx = specification.kind == "weka"
    if specification.kind not in {"weka", "freeinference"}:
        raise ValueError(f"Unsupported session format: {specification.kind}")
    qualify = qualify_agentx if agentx else qualify_free
    catalog = []
    accepted = []
    for ordinal, session in enumerate(sessions):
        reasons = qualify(session)
        identity = session["id"] if agentx else session["session_id"]
        catalog.append(
            {
                "source_ordinal": ordinal,
                "session_id": identity,
                "excluded_reasons": reasons,
            }
        )
        if not reasons:
            accepted.append((ordinal, session))
    if not agentx:
        accepted.sort(
            key=lambda item: (item[1]["requests"][0]["start_ms"], item[1]["session_id"])
        )
    if count <= 0 or len(accepted) < 2 * count:
        raise ValueError(
            f"Need {2 * count} complete qualified sessions; found {len(accepted)}"
        )
    manifest = specification.metadata()
    manifest.update(
        {
            "format": "agentic_mooncake",
            "arrival_time_multiplier": arrival_scale,
            "execution": "closed-loop completion/tool gaps; serial top-level plays, native subagent spawn/join"
            if agentx
            else "closed-loop sequential sessions; observed global root arrivals",
            "agentic_lanes": 1 if agentx else None,
            "qualification": catalog,
            "source_sessions": len(sessions),
            "qualified_sessions": len(accepted),
            "windows": {},
            "model_hash_isolation": True,
            "input_count_basis": "proxy 64-token blocks (source counting artifacts)"
            if agentx
            else "o200k_base role-token sum; provider-reported completion tokens; private partial input tails",
        }
    )
    for split, start in (("tuning", 0), ("evaluation", count)):
        selected = accepted[start : start + count]
        destination = output / f"{split}-trace.jsonl"
        raw = output / f"{split}-source.jsonl"
        raw.write_text("".join(json.dumps(session) + "\n" for _, session in selected))
        if agentx:
            binary = ROOT / "native/target/release/kv-eviction-replay"
            subprocess.run(
                [str(binary), "import-weka", str(raw), str(destination)], check=True
            )
            objects = [
                json.loads(line) for line in destination.read_text().splitlines()
            ]
            header, rows = objects[0], objects[1:]
        else:
            rows = free_rows([session for _, session in selected])
            header = {
                "schema": "dynamo.agentic_mooncake",
                "version": 2,
                "block_size": 16,
                "hash_id_scope": "local",
                "source": {
                    "format": "freeinference-positive-sequential-v1",
                    "digest": specification.uncompressed_sha256 or specification.sha256,
                },
            }
        write_agentic(destination, header, rows, arrival_scale)
        minimum = max(
            math.ceil((row["input_length"] + row["output_length"]) / 512) * 512
            for row in rows
        )
        manifest["windows"][split] = {
            "source_ordinals": [ordinal for ordinal, _ in selected],
            "session_ids": [catalog[ordinal]["session_id"] for ordinal, _ in selected],
            "sessions": len(selected),
            "requests": len(rows),
            "sha256": digest(destination),
            "source_sha256": digest(raw),
            "minimum_cache_tokens": minimum,
            "cold_start": True,
        }
    return manifest
