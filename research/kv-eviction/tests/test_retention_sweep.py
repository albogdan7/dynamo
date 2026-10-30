"""End-to-end fixture: plan, run, audit and report the retention sweep."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import retention_report
from kv_experiment import retention, retention_evidence
from kv_experiment.agentic import oracle_gaps, unbounded_blocks, write_split
from kv_experiment.inventory import sha256
from kv_experiment.runtime import NATIVE
from kv_experiment.traces import ROOT

pytestmark = pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1" or not NATIVE.exists(),
    reason="Native adapter and Docker required",
)


def play(name, gap):
    """Two sessions; each returns after a recorded gap with a longer prefix."""
    rows = []
    for session, base in (("s", 1), ("t", 50)):
        previous = None
        for turn in range(3):
            hashes = list(range(base, base + 8 * (turn + 1)))
            row = {
                "request_id": f"{name}:{session}:{turn}",
                "play_id": name,
                "session_id": f"{name}:{session}",
                "model": "m",
                "input_length": 64 * len(hashes),
                "output_length": 3,
                "hash_ids": [hash(f"{name}:{h}") % 2**40 for h in hashes],
                "not_before_ms": 0.0,
                "dependencies": [],
            }
            if previous:
                row["dependencies"] = [
                    {
                        "request_id": previous,
                        "trigger": "completion",
                        "relation": "sequence",
                        "delay_ms": gap,
                    }
                ]
            rows.append(row)
            previous = row["request_id"]
    return rows


@pytest.fixture
def sweep(tmp_path, monkeypatch):
    header = {
        "schema": "dynamo.agentic_mooncake",
        "version": 2,
        "block_size": 64,
        "hash_id_scope": "local",
        "source": {"format": "test", "digest": "0" * 64},
    }
    rows = [r for i, gap in enumerate((40, 400, 4000, 40)) for r in play(f"p{i}", gap)]
    splits = {"tuning": ["p0", "p1"], "evaluation": ["p2", "p3"]}
    inventory = {
        "plays": 4,
        "requests": len(rows),
        "zero_output_requests": 0,
        "splits": {},
    }
    for split, plays in splits.items():
        path = tmp_path / f"{split}.jsonl"
        selected = write_split(path, header, rows, plays)
        gaps, broken = oracle_gaps(selected)
        oracle = tmp_path / f"oracle-{split}.json"
        oracle.write_text(json.dumps(gaps))
        inventory["splits"][split] = {
            "path": str(path),
            "sha256": sha256(path),
            "plays": plays,
            "requests": len(selected),
            "non_chain_sessions": broken,
            "oracle_path": str(oracle),
            "oracle_sha256": sha256(oracle),
            "unbounded_cache_blocks": unbounded_blocks(selected, 512),
        }
    profile = json.loads((ROOT / "profiles/interactive.json").read_text())
    profile["rank"]["timing_model"] = {
        "type": "fixed",
        "prefill_ms": 10,
        "decode_ms": 20,
    }
    (tmp_path / "profile.json").write_text(json.dumps(profile))
    calibration = tmp_path / "calibration.json"
    calibration.write_text(json.dumps({"quadratic": [0.0, 0.02, 0.5]}))
    config = json.loads((ROOT / "configs/agentic-retention.json").read_text())
    config.update(
        timing_profile=str(tmp_path / "profile.json"),
        cache_blocks=5,
        lanes=[1, 2],
        parity_fixture={"plays": 1, "lanes": 1},
        oracle_slack_ms=0,
    )
    config["telemetry"] = {
        "sample_interval_ms": 50,
        "finer_check": {"sample_interval_ms": 5, "plays": 1, "lanes": 1},
    }
    config["continuum"]["reload_calibration"] = str(calibration)
    config["decision_rule"]["split"] = "evaluation"
    monkeypatch.setattr(retention, "CHECK_LANES", 2)
    monkeypatch.setattr(retention_evidence, "CHECK_LANES", 2)
    for name, value in (("config.json", config), ("inventory.json", inventory)):
        (tmp_path / name).write_text(json.dumps(value))
    return tmp_path


def test_sweep_audit_and_report(sweep):
    run = sweep / "run"
    status = retention.run(sweep / "config.json", run, 4, sweep / "inventory.json")
    assert status["failed"] == []
    assert len(status["jobs"]) == 2 * 2 * len(retention.POLICY_ORDER) + 2 + 4 + 2
    results = retention_evidence.build(run, sweep / "evidence")
    assert all(all(files.values()) for files in results["parity"].values())
    cell = results["results"]["evaluation"]["lanes-2"]
    assert cell["unbounded"]["evictions"] == 0 and cell["lru"]["evictions"] > 0
    # The oracle never reuses less than LRU on this fixture.
    assert cell["oracle"]["reused_input_tokens"] >= cell["lru"]["reused_input_tokens"]
    assert set(results["decision"]["levels"]) == {1, 2} or set(
        results["decision"]["levels"]
    ) == {"1", "2"}
    page = sweep / "report/retention.html"
    first = retention_report.build(sweep / "evidence", page)
    assert first["verdict"] == results["decision"]["verdict"]
    content = page.read_bytes()
    retention_report.build(sweep / "evidence", page)
    assert page.read_bytes() == content
