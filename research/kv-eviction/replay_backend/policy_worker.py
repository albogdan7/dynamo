"""JSON policy transport for trusted local experiments, not an OS sandbox.

Each call gets a fresh policy instance: tentative engine admissions can roll
back cache state. Policies must be deterministic, stateless functions of the
provided candidates. Runtime/memory/filesystem confinement is a separate task.
"""

import contextlib
import hashlib
import json
from pathlib import Path
import sys


def main():
    path = Path(sys.argv[1]).resolve()
    source = path.read_bytes()
    code = compile(source, str(path), "exec")

    def fresh_policy():
        namespace = {"__name__": "candidate_policy", "__file__": str(path)}
        exec(code, namespace)
        return namespace["EvictionPolicy"]()

    with contextlib.redirect_stdout(sys.stderr):
        fresh_policy()
    print(json.dumps({"status": "ready", "source_sha256": hashlib.sha256(source).hexdigest()}), flush=True)
    for line in sys.stdin:
        try:
            candidates = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                victim = fresh_policy().select_victim(candidates)
            if type(victim) is not int or victim not in {b["block_id"] for b in candidates}:
                raise ValueError("policy must return an eligible integer block_id")
            result = {"victim_id": victim}
        except Exception as error:
            result = {"error": str(error)}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()

