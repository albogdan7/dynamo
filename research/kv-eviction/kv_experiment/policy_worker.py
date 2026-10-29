"""Runs inside the policy container; the host scorer never imports policy code."""

import importlib.util
import json
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
    candidates = json.loads(line)
    error_kind = "policy_exception"
    try:
        victim = policy.select_victim(candidates)
        error_kind = "invalid_decision"
        if type(victim) is not int or victim not in {
            row["block_id"] for row in candidates
        }:
            raise ValueError("select_victim must return an integer candidate block_id")
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
    print(json.dumps({"victim_id": victim}), flush=True)
