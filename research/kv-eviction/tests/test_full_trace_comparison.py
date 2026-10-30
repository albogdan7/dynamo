"""End-to-end fixture for the full-trace sweep: run, parity, failures and resume."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import baseline_report
from kv_experiment import baseline_evidence, comparison
from kv_experiment.inventory import sha256
from kv_experiment.runtime import NATIVE
from kv_experiment.traces import ROOT

pytestmark = pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1" or not NATIVE.exists(),
    reason="Native adapter and Docker required",
)


@pytest.fixture
def sweep(tmp_path):
    """A ready eviction-heavy trace, a blocked trace and a frozen configuration."""
    rows = [
        {
            "timestamp": index * 15,
            "input_length": 512 * len(hashes),
            "output_length": 3,
            "hash_ids": hashes,
        }
        for index, hashes in enumerate(
            [[1, 2], [1, 2, 3], [4, 5], [1, 2], [6, 7, 8], [4, 5, 9], [1, 2, 3]] * 4
        )
    ]
    recording = tmp_path / "full.jsonl"
    recording.write_text("".join(json.dumps(row) + "\n" for row in rows))
    profile = json.loads((ROOT / "profiles/interactive.json").read_text())
    profile["rank"].update(
        max_num_seqs=2,
        timing_model={"type": "fixed", "prefill_ms": 10, "decode_ms": 20},
    )
    (tmp_path / "profile.json").write_text(json.dumps(profile))
    config = {
        "block_tokens": 512,
        "timing_profile": str(tmp_path / "profile.json"),
        "slo": {"ttft_ms": 1000, "per_request_p95_itl_ms": 50},
        "telemetry": {
            "sample_interval_ms": 20,
            "finer_check": {"sample_interval_ms": 2, "requests": 10},
        },
        "policies": ["lru", "fifo", "random-0"],
        "job_timeout_seconds": 300,
        "traces": {
            "fixture": {"cache_tokens": 6 * 512},
            "blocked": {"cache_tokens": 512},
        },
    }

    def summary(status, requests, cache_tokens, blocked):
        return {
            "status": status,
            "cache_tokens": cache_tokens,
            "capacity_blocks": cache_tokens // 512,
            "observed": {"requests": requests},
            "publisher_count_checks": {},
            "publisher_counts_match": True,
            "replayable_requests": requests - blocked,
            "blocked_requests": blocked,
            "blocked_excluding_capacity": 0,
            "cache_tokens_to_fit_every_request": 2048,
            "flag_counts": {"infeasible": blocked},
            "blocker_counts": {"infeasible": blocked} if blocked else {},
            "flag_definitions": {},
            "revision": "fixture",
            "license": "fixture",
        }

    inventory = {
        "traces": {
            "fixture": {
                **summary("ready", len(rows), 6 * 512, 0),
                "recordings": [
                    {
                        "id": "full",
                        "path": str(recording),
                        "sha256": sha256(recording),
                        "format": "mooncake",
                        "source_block_size": 512,
                        "requests": len(rows),
                        "timing": "original fixed arrivals",
                    }
                ],
            },
            "blocked": {**summary("blocked", 1, 512, 1), "recordings": []},
        }
    }
    for name, value in (("config.json", config), ("inventory.json", inventory)):
        (tmp_path / name).write_text(json.dumps(value))
    return tmp_path


def test_sweep_runs_every_policy_and_check_then_resumes(sweep):
    run = sweep / "run"
    status = comparison.run(sweep / "config.json", sweep / "inventory.json", run, 4)
    assert status["failed"] == []
    expected = {
        "lru",
        "fifo",
        "random-0",
        "lru-repeat",
        "lru-callback",
        "lru-no-telemetry",
        "cadence-check/telemetry",
        "cadence-check/fine-telemetry",
    }
    assert set(status["jobs"]) == {f"fixture/{job}" for job in expected}
    assert not (run / "blocked").exists()
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["blocked_traces"]["blocked"]["blocker_counts"] == {"infeasible": 1}
    lru, fifo = run / "fixture/lru", run / "fixture/fifo"
    assert json.loads((lru / "metrics.json").read_text())["evictions"] > 0
    assert (fifo / "policy.py").read_text() == (run / "policies/fifo.py").read_text()
    for name in ("requests.jsonl", "tokens.jsonl", "evictions.json"):
        for check in ("lru-repeat", "lru-callback", "lru-no-telemetry"):
            assert (lru / name).read_bytes() == (
                run / "fixture" / check / name
            ).read_bytes()
    assert (lru / "occupancy.jsonl").read_bytes() == (
        run / "fixture/lru-repeat/occupancy.jsonl"
    ).read_bytes()
    assert json.loads((lru / "audit.json").read_text())["occupancy_audited"]
    # A resumed run re-verifies hashes and skips completed, audited jobs.
    again = comparison.run(sweep / "config.json", sweep / "inventory.json", run, 4)
    assert {item["status"] for item in again["jobs"].values()} == {"skipped"}


def test_failed_policy_is_preserved_without_a_score(sweep, monkeypatch):
    table = comparison.policy_table

    def failing(config):
        policies = table(config)
        policies["fifo"]["source"] = (
            "class EvictionPolicy:\n"
            "    def select_victim(self, blocks):\n"
            "        raise RuntimeError('boom')\n"
        )
        return policies

    monkeypatch.setattr(comparison, "policy_table", failing)
    run = sweep / "run"
    status = comparison.run(sweep / "config.json", sweep / "inventory.json", run, 4)
    assert status["failed"] == ["fixture/fifo"]
    failure = json.loads((run / "fixture/fifo/failure.json").read_text())
    assert failure["error_type"] == "PolicyError" and "boom" in failure["error"]
    assert not (run / "fixture/fifo/metrics.json").exists()
    assert (run / "fixture/lru/audit.json").is_file()
    # A retry keeps the failed attempt and skips completed, audited jobs.
    again = comparison.run(sweep / "config.json", sweep / "inventory.json", run, 4)
    assert again["failed"] == ["fixture/fifo"]
    assert (run / "fixture/fifo.attempt-1/failure.json").is_file()
    assert again["jobs"]["fixture/lru"]["status"] == "skipped"
    with pytest.raises(ValueError, match="Failed job"):
        baseline_evidence.build(run, sweep / "evidence")
    # Frozen sources cannot be swapped inside an existing run.
    monkeypatch.setattr(comparison, "policy_table", table)
    with pytest.raises(ValueError, match="Frozen run input"):
        comparison.run(sweep / "config.json", sweep / "inventory.json", run, 4)


def test_audit_and_report_regenerate_only_from_verified_evidence(sweep):
    run, evidence = sweep / "run", sweep / "evidence"
    comparison.run(sweep / "config.json", sweep / "inventory.json", run, 4)
    results = baseline_evidence.build(run, evidence)
    assert results["audited_jobs"] == 8
    fixture = results["traces"]["fixture"]
    assert set(fixture["policies"]) == {"lru", "fifo", "random-0"}
    assert all(all(files.values()) for files in fixture["parity"].values())
    assert results["traces"]["blocked"]["status"] == "blocked"
    with pytest.raises(ValueError, match="exists"):
        baseline_evidence.build(run, evidence)
    page = sweep / "report/baselines.html"
    summary = baseline_report.build(evidence, page)
    assert summary["traces_reported"] == ["fixture"]
    first = page.read_bytes()
    table = page.with_suffix(".csv").read_text().splitlines()
    assert len(table) == 1 + 3 * len(baseline_report.ROWS)
    baseline_report.build(evidence, page)
    assert page.read_bytes() == first
    # Tampered compact evidence or raw replay files are refused.
    results_file = evidence / "results.json"
    results_file.write_text(results_file.read_text().replace('"fifo"', '"FIFO"', 1))
    with pytest.raises(ValueError, match="changed"):
        baseline_report.build(evidence, page)
    tokens = run / "fixture/fifo/tokens.jsonl"
    lines = tokens.read_text().splitlines(keepends=True)
    tokens.write_text("".join(lines[:-1]))
    with pytest.raises(ValueError):
        baseline_evidence.build(run, sweep / "evidence-again")
