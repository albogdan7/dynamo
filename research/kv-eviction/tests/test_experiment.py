"""Hand-calculated metrics and actual native/container boundary checks."""

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment import LRU, submission_dir
from kv_experiment.metrics import score
from kv_experiment.runtime import NATIVE, PolicyProcess, replay, send_json
from kv_experiment.traces import ROOT


def metric_fixture(tmp_path):
    # Request A: TTFT=100, gaps=[10]*19+[200]; mean=19.5, p95=10.
    # Request B: one output token, TTFT=1000, no gap samples.
    # Request C: failed with no tokens; remains in the SLO denominator.
    times = [100 + index * 10 for index in range(20)] + [490]
    rows = []
    for name, arrival, outputs, status in (
        ("a", 0, 21, "completed"),
        ("b", 1000, 1, "completed"),
        ("c", 0, 0, "failed"),
    ):
        rows.append(
            {
                "uuid": name,
                "request_id": name,
                "arrival_time_ms": arrival,
                "terminal_time_ms": 2000,
                "output_length": outputs,
                "requested_output_length": outputs if outputs else 5,
                "terminal_status": status,
                "input_length": 512,
                "admission_history": [{"reused_input_tokens": 0}],
            }
        )
    events = [{"uuid": "a", "time_ms": timestamp} for timestamp in times]
    events.append({"uuid": "b", "time_ms": 2000})
    for name, records in (("requests", rows), ("tokens", events)):
        (tmp_path / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in records)
        )
    (tmp_path / "native-report.json").write_text(
        json.dumps(
            {
                "committed_prefill_tokens": 1536,
                "num_requests": 3,
                "total_input_tokens": 1536,
                "total_output_tokens": 22,
                "completed_requests": 2,
            }
        )
    )
    (tmp_path / "evictions.json").write_text('{"evictions":1}')
    return tmp_path


def test_individual_gap_quantiles_and_slo_boundary(tmp_path):
    result = score(metric_fixture(tmp_path), 1000, 10, write_request_scores=True)
    saved_scores = (tmp_path / "request-scores.jsonl").read_bytes()
    assert result["mean_ttft_ms"] == 550
    assert result["p95_ttft_ms"] == 1000
    assert result["mean_itl_ms"] == 19.5
    assert result["p95_itl_ms"] == 10  # Cannot substitute per-request mean 19.5.
    assert result["itl_samples"] == 20
    assert result["goodput_rps"] == 1  # Two passing / two seconds including drain.
    assert result["slo_attainment_pct"] == pytest.approx(200 / 3)
    assert score(tmp_path, 999, 10)["slo_passes"] == 1
    assert score(tmp_path, 1000, 9)["slo_passes"] == 1
    assert (tmp_path / "request-scores.jsonl").read_bytes() == saved_scores


def test_missing_token_events_fail_instead_of_estimating_itl(tmp_path):
    metric_fixture(tmp_path)
    (tmp_path / "tokens.jsonl").write_text("")
    with pytest.raises(ValueError, match="count disagrees"):
        score(tmp_path, 1000, 50)


def test_submission_ids_never_overwrite(tmp_path):
    parent = tmp_path / "submissions"
    first, second = submission_dir(parent), submission_dir(parent)
    assert first != second
    assert first.is_dir() and second.is_dir()


def test_pipe_write_has_a_deadline():
    read_fd, write_fd = os.pipe()
    with (
        os.fdopen(write_fd, "wb") as writer,
        os.fdopen(read_fd, "rb"),
        pytest.raises(TimeoutError),
    ):
        send_json(writer, "x" * (1024 * 1024), timeout=0.1)


@pytest.fixture
def native_inputs(tmp_path):
    if not NATIVE.exists():
        pytest.skip("Build native replay with prepare_native.py first")
    rows = [
        {
            "timestamp": index * 10000,
            "input_length": 1024,
            "output_length": 3,
            "hash_ids": hashes,
        }
        for index, hashes in enumerate(([1, 2], [1, 3], [4, 5], [1, 2], [1, 3]))
    ]
    trace = tmp_path / "trace.jsonl"
    trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
    config = json.loads((ROOT / "profiles/interactive.json").read_text())
    config["rank"].update(
        num_gpu_blocks=5,
        max_num_seqs=1,
        max_num_batched_tokens=512,
        timing_model={"type": "fixed", "prefill_ms": 10, "decode_ms": 20},
    )
    profile = tmp_path / "config.json"
    profile.write_text(json.dumps(config))
    return trace, profile


def test_native_prefill_decode_queue_and_token_accounting(native_inputs, tmp_path):
    trace, profile = native_inputs
    result = replay(trace, profile, tmp_path / "native", None, 1000, 50)
    assert result["requests"] == result["completed"] == 5
    assert result["itl_samples"] == 10
    assert result["mean_itl_ms"] == result["p95_itl_ms"] == 20
    assert result["evictions"] > 0
    assert (
        result["recomputed_input_tokens"]
        == result["input_tokens"] - result["reused_input_tokens"]
    )
    # With max_num_seqs=1, simultaneous arrivals must wait for earlier completion.
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    for row in rows:
        row["timestamp"] = 0
    trace.write_text("".join(json.dumps(row) + "\n" for row in rows))
    queued = replay(trace, profile, tmp_path / "queued", None, 1000, 50)
    assert queued["mean_ttft_ms"] > result["mean_ttft_ms"]


@pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1",
    reason="Set KV_RUN_CONTAINER_TESTS=1 for real Docker isolation tests",
)
def test_container_callback_matches_native_and_replays(native_inputs, tmp_path):
    trace, profile = native_inputs
    policy = tmp_path / "policy.py"
    policy.write_text(LRU)
    expected = replay(trace, profile, tmp_path / "native", None, 1000, 50)
    for name in ("callback", "repeat"):
        actual = replay(trace, profile, tmp_path / name, policy, 1000, 50)
        assert actual["policy_calls"] == actual["evictions"] > 0
        assert {
            key: value
            for key, value in actual.items()
            if key not in {"policy_calls", "simulator_wall_seconds"}
        } == {
            key: value
            for key, value in expected.items()
            if key not in {"policy_calls", "simulator_wall_seconds"}
        }


@pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1", reason="Real Docker required"
)
def test_alternative_policy_changes_native_cache_decisions(native_inputs, tmp_path):
    trace, profile = native_inputs
    policy = tmp_path / "policy.py"
    policy.write_text(
        'class EvictionPolicy:\n    def select_victim(self, blocks):\n        return max(blocks, key=lambda b: b["lru_rank"])["block_id"]\n'
    )
    baseline = replay(trace, profile, tmp_path / "lru", None, 1000, 50)
    alternate = replay(trace, profile, tmp_path / "alternate", policy, 1000, 50)
    assert alternate["policy_calls"] > 0
    assert alternate["reused_input_tokens"] != baseline["reused_input_tokens"]


@pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1", reason="Real Docker required"
)
def test_invalid_policy_never_receives_successful_replay_metrics(
    native_inputs, tmp_path
):
    trace, profile = native_inputs
    policy = tmp_path / "policy.py"
    policy.write_text(
        "class EvictionPolicy:\n    def select_victim(self, blocks):\n        return -1\n"
    )
    output = tmp_path / "invalid"
    with pytest.raises(RuntimeError):
        replay(trace, profile, output, policy, 1000, 50)
    assert not (output / "metrics.json").exists()
    assert "ValueError" in (output / "policy-stderr.txt").read_text()


@pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1", reason="Real Docker required"
)
@pytest.mark.parametrize(
    "body,exception",
    [
        ("return -123", RuntimeError),
        ("raise ValueError('policy failed')", RuntimeError),
        ("while True: pass", TimeoutError),
        ("print('[]'); return blocks[0]['block_id']", ValueError),
    ],
)
def test_bad_policies_fail_and_cleanup(tmp_path, body, exception):
    policy = tmp_path / "policy.py"
    policy.write_text(
        "class EvictionPolicy:\n    def select_victim(self, blocks):\n        "
        + body
        + "\n"
    )
    with (tmp_path / "stderr").open("wb") as stderr, ExitStack() as stack:
        worker = PolicyProcess(policy, stderr, stack)
        name = worker.name
        with pytest.raises(exception):
            worker.choose([{"block_id": 1}])
    containers = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert not containers.stdout.strip()


@pytest.mark.skipif(
    os.environ.get("KV_RUN_CONTAINER_TESTS") != "1", reason="Real Docker required"
)
def test_policy_cannot_read_host_write_or_connect(tmp_path):
    secret = tmp_path / "withheld-secret.txt"
    secret.write_text("hidden evaluation data")
    policy = tmp_path / "policy.py"
    policy.write_text(f"""import socket
class EvictionPolicy:
    def select_victim(self, blocks):
        try:
            open({str(secret)!r}).read()
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("host file visible")
        try:
            open("/submission/escape", "w")
        except OSError:
            pass
        else:
            raise RuntimeError("filesystem writable")
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=0.2)
        except OSError:
            pass
        else:
            raise RuntimeError("network allowed")
        return blocks[0]["block_id"]
""")
    with (tmp_path / "stderr").open("wb") as stderr, ExitStack() as stack:
        worker = PolicyProcess(policy, stderr, stack)
        assert worker.choose([{"block_id": 1}]) == {"victim_id": 1}
