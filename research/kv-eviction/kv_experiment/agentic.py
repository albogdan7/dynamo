"""AgentX inputs for the agentic retention experiment (AGENTIC_RETENTION_SPEC.md).

The native Weka importer lowers every play once. Plays are then assigned to a
seeded tuning/evaluation split without looking at their contents, and each
split is written with its plays contiguous so closed-loop lanes start them in
that order. Nothing is filtered: every source request lands in exactly one split.
"""

import json
import math
import random
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from .inventory import RELEASES, fetch, sha256
from .runtime import NATIVE
from .traces import ROOT

OUTPUT = ROOT / ".cache/agentic-retention"


def read_trace(path: Path) -> tuple[dict, list[dict]]:
    with path.open() as handle:
        header = json.loads(handle.readline())
        return header, [json.loads(line) for line in handle if line.strip()]


def split_plays(rows: list[dict], seed: int, tuning_plays: int) -> dict[str, list[str]]:
    """Seeded shuffle of plays in source order; never inspects play contents."""
    plays = list(dict.fromkeys(row["play_id"] for row in rows))
    random.Random(seed).shuffle(plays)
    return {"tuning": plays[:tuning_plays], "evaluation": plays[tuning_plays:]}


def session_chains(rows: list[dict]) -> tuple[dict[str, list[dict]], list[str]]:
    """Order each session by its in-session sequence edges; report non-chains."""
    by_id = {row["request_id"]: row for row in rows}
    sessions, successors, predecessors = defaultdict(list), defaultdict(list), Counter()
    for row in rows:
        sessions[row["session_id"]].append(row)
        for edge in row.get("dependencies", []):
            parent = by_id[edge["request_id"]]
            if (
                edge["relation"] == "sequence"
                and parent["session_id"] == row["session_id"]
            ):
                successors[parent["request_id"]].append(row)
                predecessors[row["request_id"]] += 1
    chains, broken = {}, []
    for session, members in sessions.items():
        roots = [row for row in members if predecessors[row["request_id"]] == 0]
        linear = all(
            len(successors[row["request_id"]]) <= 1
            and predecessors[row["request_id"]] <= 1
            for row in members
        )
        chain, current = [], roots[0] if len(roots) == 1 and linear else None
        while current is not None:
            chain.append(current)
            following = successors[current["request_id"]]
            current = following[0] if following else None
        if len(chain) == len(members):
            chains[session] = chain
        else:
            broken.append(session)
    return chains, broken


def oracle_gaps(rows: list[dict]) -> tuple[dict[str, list[float | None]], list[str]]:
    """Recorded pause after each session's k-th request; future data, bounds only."""
    by_id = {row["request_id"]: row for row in rows}
    chains, broken = session_chains(rows)
    gaps = {}
    for session, chain in chains.items():
        values = []
        for current, following in zip(chain, chain[1:] + [None], strict=True):
            if following is None:
                values.append(None)
                continue
            (edge,) = [
                e
                for e in following["dependencies"]
                if e["request_id"] == current["request_id"]
                and e["relation"] == "sequence"
            ]
            assert by_id[edge["request_id"]] is current
            values.append(edge["delay_ms"])
        gaps[session] = values
    return gaps, broken


def write_split(
    path: Path, header: dict, rows: list[dict], plays: list[str]
) -> list[dict]:
    by_play = defaultdict(list)
    for row in rows:
        by_play[row["play_id"]].append(row)
    # The native loader requires contiguous play ordinals; lanes start plays in
    # this order, so renumber them in the assigned split order.
    selected = [
        {**row, "source_play_ordinal": ordinal}
        for ordinal, play in enumerate(plays)
        for row in by_play[play]
    ]
    with path.open("w") as handle:
        handle.write(json.dumps(header) + "\n")
        for row in selected:
            handle.write(json.dumps(row) + "\n")
    return selected


def unbounded_blocks(rows: list[dict], block_tokens: int) -> int:
    """Capacity that cannot evict: one block per distinct source hash (an engine
    page is identified by its last full source block), plus private tails and
    every output page."""
    distinct = {block for row in rows for block in row["hash_ids"]}
    private = sum(2 + math.ceil(row["output_length"] / block_tokens) for row in rows)
    return len(distinct) + private


def prepare(config: dict) -> dict:
    """Import, split and inventory AgentX; idempotent apart from the costly import."""
    (source,) = fetch(config["trace"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    imported = OUTPUT / "full-import.jsonl"
    if not imported.exists():
        partial = imported.with_name("full-import.partial.jsonl")
        subprocess.run(
            [str(NATIVE), "import-weka", str(source), str(partial)], check=True
        )
        partial.with_suffix(".import.json").rename(imported.with_suffix(".import.json"))
        partial.rename(imported)
    header, rows = read_trace(imported)
    published = RELEASES[config["trace"]]["published"]
    plays = list(dict.fromkeys(row["play_id"] for row in rows))
    if (len(plays), len(rows)) != (published["plays"], published["requests"]):
        raise ValueError(
            "Imported AgentX does not cover the complete published release"
        )
    capacity_tokens = config["cache_blocks"] * config["block_tokens"]
    largest = max(row["input_length"] + row["output_length"] for row in rows)
    if math.ceil(largest / config["block_tokens"]) > config["cache_blocks"]:
        raise ValueError("A request exceeds the frozen cache; capacity is never raised")
    assignment = split_plays(
        rows, config["split"]["seed"], config["split"]["tuning_plays"]
    )
    inventory = {
        "source_sha256": sha256(source),
        "import": json.loads(imported.with_suffix(".import.json").read_text()),
        "import_sha256": sha256(imported),
        "plays": len(plays),
        "requests": len(rows),
        "zero_output_requests": sum(row["output_length"] == 0 for row in rows),
        "largest_context_tokens": largest,
        "cache_tokens": capacity_tokens,
        "splits": {},
    }
    covered = []
    for split, members in assignment.items():
        path = OUTPUT / f"{split}.jsonl"
        selected = write_split(path, header, rows, members)
        gaps, broken = oracle_gaps(selected)
        oracle = OUTPUT / f"oracle-{split}.json"
        oracle.write_text(json.dumps(gaps, sort_keys=True) + "\n")
        covered.extend(row["request_id"] for row in selected)
        inventory["splits"][split] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256(path),
            "plays": members,
            "requests": len(selected),
            "sessions": len(gaps) + len(broken),
            "non_chain_sessions": broken,
            "oracle_path": str(oracle.relative_to(ROOT)),
            "oracle_sha256": sha256(oracle),
            "unbounded_cache_blocks": unbounded_blocks(
                selected, config["block_tokens"]
            ),
            "zero_output_requests": sum(row["output_length"] == 0 for row in selected),
        }
    if sorted(covered) != sorted(row["request_id"] for row in rows) or len(
        set(covered)
    ) != len(rows):
        raise ValueError("Splits do not cover every request exactly once")
    (OUTPUT / "inventory.json").write_text(json.dumps(inventory, indent=1) + "\n")
    return inventory
