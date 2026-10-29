"""Integration tests against the compiled engine, never a fake replay adapter."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
BINARY = ROOT / "target" / "debug" / "kv-policy-replay"


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BINARY.exists():
            raise RuntimeError("Build the runner with cargo build --locked first")

    def replay(self, policy="lru", spec=None, source=None):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            spec_path = ROOT / "fixtures" / "pressure.json"
            if spec is not None:
                spec_path = directory / "spec.json"
                spec_path.write_text(json.dumps(spec))
            policy_path = "-" if policy == "native" else str(ROOT / "policies" / f"{policy}.py")
            if source is not None:
                path = directory / "policy.py"
                path.write_text(source)
                policy_path = str(path)
            return subprocess.run(
                [str(BINARY), str(spec_path), policy_path, sys.executable],
                capture_output=True, text=True, timeout=15,
            )

    def successful(self, **kwargs):
        completed = self.replay(**kwargs)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_python_lru_matches_native_engine(self):
        native = self.successful(policy="native")
        custom = self.successful()
        self.assertEqual(native["per_request"], custom["per_request"])
        self.assertEqual(custom["summary"]["completed_requests"], 12)
        self.assertEqual(native["summary"]["committed_prefill_tokens"], custom["summary"]["committed_prefill_tokens"])
        self.assertTrue(custom["eviction_decision_attempts"])

    def test_custom_policy_changes_actual_eviction_and_reuse(self):
        lru = self.successful()
        mru = self.successful(policy="mru")
        self.assertNotEqual(lru["eviction_decision_attempts"][0]["victim_id"], mru["eviction_decision_attempts"][0]["victim_id"])
        self.assertNotEqual(lru["summary"]["committed_prefill_tokens"], mru["summary"]["committed_prefill_tokens"])
        self.assertNotEqual(lru["summary"]["prefix_cache_reused_ratio"], mru["summary"]["prefix_cache_reused_ratio"])
        for record in mru["eviction_decision_attempts"]:
            self.assertIn(record["victim_id"], [b["block_id"] for b in record["candidates"]])
            for candidate in record["candidates"]:
                self.assertEqual(set(candidate), {"block_id", "lru_rank", "size_tokens", "prefix_tokens"})

    def test_repeated_run_has_identical_decisions_and_request_metrics(self):
        first = self.successful()
        second = self.successful()
        self.assertEqual(first["eviction_decision_attempts"], second["eviction_decision_attempts"])
        self.assertEqual(first["per_request"], second["per_request"])

    def test_burst_queues_through_decode_completion(self):
        spec = json.loads((ROOT / "fixtures" / "pressure.json").read_text())
        spec["requests"] = spec["requests"][:2]
        for request in spec["requests"]:
            request["arrival_time_ms"] = 0.0
        result = self.successful(spec=spec)
        first, second = result["per_request"]
        self.assertEqual(first["first_token_ms"], 2.0)
        self.assertEqual(first["last_token_ms"], 5.0)
        self.assertGreaterEqual(second["first_admit_ms"], first["last_token_ms"])
        self.assertGreater(second["ttft_ms"], first["ttft_ms"])

    def test_invalid_victim_aborts_without_a_score(self):
        result = self.replay(source="class EvictionPolicy:\n def select_victim(self, blocks): return -1\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("eligible integer block_id", result.stderr)

    def test_policy_namespace_is_reset_for_each_decision(self):
        result = self.successful(source=(
            "calls = 0\n"
            "class EvictionPolicy:\n"
            " def select_victim(self, blocks):\n"
            "  global calls\n"
            "  calls += 1\n"
            "  return blocks[0]['block_id'] if calls == 1 else -1\n"
        ))
        self.assertGreater(len(result["eviction_decision_attempts"]), 1)

    def test_hung_policy_aborts_without_a_score(self):
        result = self.replay(source="import time\nclass EvictionPolicy:\n def select_victim(self, blocks): time.sleep(30)\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("timed out", result.stderr)

    def test_multiple_workers_are_rejected(self):
        spec = json.loads((ROOT / "fixtures" / "pressure.json").read_text())
        spec["topology"]["workers"]["initial_workers"] = 2
        result = self.replay(spec=spec)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one aggregated worker", result.stderr)


if __name__ == "__main__":
    unittest.main()
