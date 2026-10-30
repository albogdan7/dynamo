"""Check occupancy arithmetic, conservation and telemetry/serving independence."""

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evidence.audit_run import audit
from kv_experiment.occupancy import summarize
from kv_experiment.runtime import NATIVE, replay
from kv_experiment.traces import ROOT


def sample(ordinal, kind, at, active, inactive, waiting, capacity=4, preemptions=0):
    return {
        "ordinal": ordinal,
        "kind": kind,
        "sampled_at_ms": at,
        "preemptions": preemptions,
        "router_pending_requests": 0,
        "ranks": [
            {
                "worker_id": 0,
                "dp_rank": 0,
                "active_blocks": active,
                "inactive_blocks": inactive,
                "free_blocks": capacity - active - inactive,
                "total_blocks": capacity,
                "running_requests": int(active > 0),
                "waiting_requests": waiting,
            }
        ],
    }


@pytest.fixture
def samples():
    # Held durations are 10, 10, 5 and 0 ms. The last periodic interval is idle.
    rows = [
        sample(0, "baseline", 0, 1, 1, 1),
        sample(1, "periodic", 10, 4, 0, 2, preemptions=1),
        sample(2, "periodic", 20, 0, 4, 0),
        sample(3, "final", 25, 0, 0, 0),
    ]
    for previous, row in zip([rows[0], *rows], rows, strict=False):
        row["interval_start_ms"] = previous["sampled_at_ms"]
    return rows


def test_time_weighting_uses_held_durations_not_sample_counts(samples):
    summary = summarize(samples, 10, 25)
    whole = summary["ranks"]["0:0"]["whole_run"]
    # Physical values 2, 4, 4 for 10, 10, 5 ms; a sample average would be 2.5.
    assert whole["physical_blocks"]["mean"] == pytest.approx(80 / 25)
    assert whole["physical_blocks"]["p95"] == 4
    assert whole["physical_blocks"]["time_fraction_at_or_above_95pct"] == 15 / 25
    assert whole["physical_blocks"]["time_fraction_at_or_above_100pct"] == 15 / 25
    # Active values 1, 4, 0: a physically full reclaimable cache is not active pressure.
    assert whole["active_blocks"]["mean"] == pytest.approx(50 / 25)
    assert whole["active_blocks"]["time_fraction_at_or_above_100pct"] == 10 / 25
    assert whole["waiting_requests"]["peak_sampled"] == 2
    assert summary["observation_ms"] == 25
    assert summary["preemptions"] == 1
    # The interval starting at 20 ms has no request in the system.
    assert summary["request_active_ms"] == 20
    active = summary["ranks"]["0:0"]["request_active"]
    assert active["physical_blocks"]["mean"] == pytest.approx(60 / 20)
    assert active["physical_blocks"]["peak_sampled"] == 4
    assert active["inactive_blocks"]["mean"] == pytest.approx(10 / 20)


def test_conservation_truncation_and_cadence_errors_are_rejected(samples):
    broken = copy.deepcopy(samples)
    broken[1]["ranks"][0]["free_blocks"] += 1
    with pytest.raises(ValueError, match="conservation"):
        summarize(broken, 10, 25)
    with pytest.raises(ValueError, match="last terminal"):
        summarize(samples[:-1], 10, 25)
    skipped = copy.deepcopy(samples)
    del skipped[1]
    with pytest.raises(ValueError, match="ordinals"):
        summarize(skipped, 10, 25)
    with pytest.raises(ValueError, match="cadence"):
        summarize(samples, 7, 25)


@pytest.fixture
def native_fixture(tmp_path):
    if not NATIVE.exists():
        pytest.skip("Build the native adapter first")
    trace = tmp_path / "trace.jsonl"
    rows = [
        {
            "timestamp": index * 15,
            "input_length": 512 * len(hashes),
            "output_length": 6,
            "hash_ids": hashes,
        }
        for index, hashes in enumerate(
            [[1, 2], [1, 2, 3], [4, 5], [1, 2], [6, 7, 8], [4, 5, 9], [1, 2, 3]] * 3
        )
    ]
    trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
    config = json.loads((ROOT / "profiles/interactive.json").read_text())
    config["trace"] = {"format": "mooncake", "source_block_size": 512}
    config["rank"].update(
        num_gpu_blocks=6,
        max_num_seqs=2,
        timing_model={"type": "fixed", "prefill_ms": 10, "decode_ms": 20},
    )

    def configured(name, interval_ms=None):
        value = copy.deepcopy(config)
        if interval_ms:
            value["telemetry"] = {"sample_interval_ms": interval_ms}
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value))
        return path

    return tmp_path, trace, configured


def test_native_telemetry_does_not_change_serving_and_audits(native_fixture):
    root, trace, configured = native_fixture
    plain = replay(trace, configured("plain"), root / "plain", None, 1000, 50)
    measured = replay(
        trace, configured("measured", 50), root / "measured", None, 1000, 50
    )
    assert plain["evictions"] > 0
    for name in ("requests.jsonl", "tokens.jsonl", "evictions.json"):
        assert (root / "plain" / name).read_bytes() == (
            root / "measured" / name
        ).read_bytes()
    ignored = {"occupancy", "simulator_wall_seconds"}
    assert {k: v for k, v in measured.items() if k not in ignored} == {
        k: v for k, v in plain.items() if k not in ignored
    }
    occupancy = measured["occupancy"]
    assert occupancy["observation_ms"] == pytest.approx(
        plain["observation_duration_ms"]
    )
    assert occupancy["ranks"]["0:0"]["capacity_blocks"] == 6
    assert audit(root / "measured")["occupancy_audited"]


def test_finer_cadence_changes_only_sampling_error(native_fixture):
    root, trace, configured = native_fixture
    coarse = replay(trace, configured("coarse", 50), root / "coarse", None, 1000, 50)
    fine = replay(trace, configured("fine", 1), root / "fine", None, 1000, 50)
    assert (root / "coarse/tokens.jsonl").read_bytes() == (
        root / "fine/tokens.jsonl"
    ).read_bytes()
    left = coarse["occupancy"]["ranks"]["0:0"]["whole_run"]["physical_blocks"]
    right = fine["occupancy"]["ranks"]["0:0"]["whole_run"]["physical_blocks"]
    # Fixed 10/20 ms steps: 1 ms sampling is effectively exact here.
    assert abs(left["mean_pct"] - right["mean_pct"]) < 10
    assert fine["occupancy"]["samples"] > coarse["occupancy"]["samples"]
