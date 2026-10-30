"""Runs inside the policy container; the host scorer never imports policy code."""

import importlib.util
import json
import math
import sys

try:
    spec = importlib.util.spec_from_file_location("policy", "/submission/policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = module.EvictionPolicy()
except Exception as error:
    print(
        json.dumps(
            {
                "error_kind": "policy_exception",
                "error_type": type(error).__name__,
                "message": str(error),
            }
        ),
        flush=True,
    )
    raise
print(json.dumps({"status": "ready"}), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    error_kind = "policy_exception"
    try:
        if isinstance(message, dict) and set(message) == {"pause"}:
            # Retention: protect this request's KV until a time, or return None.
            until = policy.on_pause(message["pause"])
            error_kind = "invalid_decision"
            if until is not None and (
                type(until) not in (int, float) or not math.isfinite(until)
            ):
                raise ValueError("on_pause must return a finite number or None")
            response = {"protect_until_ms": until}
        else:
            candidates = message
            victim = policy.select_victim(candidates)
            error_kind = "invalid_decision"
            if type(victim) is not int or victim not in {
                row["block_id"] for row in candidates
            }:
                raise ValueError(
                    "select_victim must return an integer candidate block_id"
                )
            response = {"victim_id": victim}
    except Exception as error:
        print(
            json.dumps(
                {
                    "error_kind": error_kind,
                    "error_type": type(error).__name__,
                    "message": str(error),
                }
            ),
            flush=True,
        )
        raise
    print(json.dumps(response), flush=True)
