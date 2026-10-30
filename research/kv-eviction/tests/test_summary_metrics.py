"""Exact token summaries must reproduce the token-event scorer and audit."""

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evidence.audit_run import audit
from kv_experiment.runtime import NATIVE, replay
from kv_experiment.traces import ROOT

pytestmark = pytest.mark.skipif(not NATIVE.exists(), reason="Build the native adapter")


def test_summary_scoring_matches_token_events(tmp_path):
    rows = [
        {
            "timestamp": i * 7,
            "input_length": 512 * len(h),
            "output_length": n,
            "hash_ids": h,
        }
        for i, (h, n) in enumerate(
            [([1, 2], 9), ([1, 2, 3], 1), ([4, 5], 30), ([1, 2], 0), ([6, 7, 8], 17)]
            * 5
        )
    ]
    trace = tmp_path / "trace.jsonl"
    trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
    results = {}
    for mode in ("events", "summary"):
        config = json.loads((ROOT / "profiles/interactive.json").read_text())
        config["rank"].update(
            num_gpu_blocks=6,
            max_num_seqs=3,
            timing_model={"type": "fixed", "prefill_ms": 10, "decode_ms": 20},
        )
        config["trace"] = {"format": "mooncake", "source_block_size": 512}
        config["telemetry"] = {"sample_interval_ms": 25}
        if mode == "summary":
            config["token_capture"] = "summary"
        path = tmp_path / f"{mode}.json"
        path.write_text(json.dumps(config))
        results[mode] = replay(trace, path, tmp_path / mode, None, 40, 25)
        assert audit(tmp_path / mode)["matches"]
    assert (tmp_path / "events/requests.jsonl").read_bytes() == (
        tmp_path / "summary/requests.jsonl"
    ).read_bytes()
    events, summary = results["events"], results["summary"]
    assert 0 < summary["slo_passes"] < summary["slo_eligible_requests"]
    for key, value in events.items():
        if key in {"simulator_wall_seconds", "occupancy", "slo_warning"}:
            continue
        if isinstance(value, float):
            assert math.isclose(value, summary[key], rel_tol=1e-12), key
        else:
            assert value == summary[key], key
