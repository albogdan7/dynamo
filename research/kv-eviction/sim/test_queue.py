"""CPU-only queue regression tests: python3 -B -m unittest discover -s sim."""

import unittest

from cache import run


class LRU:
    def select_victim(self, blocks):
        return min(blocks, key=lambda b: b["last_access_time"])["block_id"]


def trace_at(*arrivals):
    return [
        {"id": i, "arrival_time": t, "input_tokens": [1] * 16}
        for i, t in enumerate(arrivals)
    ]


class QueueTests(unittest.TestCase):
    def test_idle_server_has_no_waiting(self):
        result = run(trace_at(0.0, 1.0), LRU())
        self.assertEqual(result["mean_waiting_ms"], 0.0)
        self.assertEqual(result["mean_ttft_ms"], 51.5)

    def test_burst_waits_for_first_token(self):
        # Request 1: 53 ms execution. Request 2: 53 ms wait + 50 ms hit.
        result = run(trace_at(0.0, 0.0), LRU())
        self.assertEqual(result["mean_waiting_ms"], 26.5)
        self.assertEqual(result["mean_execution_ms"], 51.5)
        self.assertEqual(result["mean_ttft_ms"], 78.0)
        self.assertEqual(result["p95_ttft_ms"], 100.5)
        self.assertEqual(result["sim_throughput_rps"], 19.42)

    def test_higher_load_increases_queueing(self):
        slow = run(trace_at(0.0, 1.0), LRU())
        fast = run(trace_at(0.0, 0.001), LRU())
        self.assertEqual(fast["overall_hit_rate"], slow["overall_hit_rate"])
        self.assertEqual(fast["mean_execution_ms"], slow["mean_execution_ms"])
        self.assertEqual(fast["mean_ttft_ms"], 77.5)
        self.assertGreater(fast["p95_ttft_ms"], slow["p95_ttft_ms"])

    def test_idle_gap_drains_queue(self):
        result = run(trace_at(0.0, 0.0, 1.0), LRU())
        self.assertEqual(result["mean_waiting_ms"], 17.67)
        self.assertEqual(result["mean_ttft_ms"], 68.67)

    def test_cache_uses_service_start_timestamp(self):
        class RecordingPolicy:
            def __init__(self):
                self.access_times = []

            def select_victim(self, blocks):
                self.access_times.append(blocks[0]["last_access_time"])
                return blocks[0]["block_id"]

        policy = RecordingPolicy()
        trace = trace_at(0.0, 0.0, 0.0)
        trace[2]["input_tokens"] = [2] * 16
        run(trace, policy, capacity_blocks=1)
        self.assertEqual(policy.access_times, [0.053])

    def test_single_request_includes_execution_in_throughput(self):
        result = run(trace_at(10.0), LRU())
        self.assertEqual(result["mean_ttft_ms"], 53.0)
        self.assertEqual(result["sim_throughput_rps"], 18.87)

    def test_invalid_trace_rejected(self):
        for trace in ([], trace_at(1.0, 0.0)):
            with self.subTest(trace=trace), self.assertRaises(ValueError):
                run(trace, LRU())


if __name__ == "__main__":
    unittest.main()
