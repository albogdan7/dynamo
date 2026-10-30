"""Native replay plus a credential-free, network-free policy container."""

import json
import math
import os
import selectors
import signal
import subprocess
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from .metrics import score
from .occupancy import read_samples, summarize
from .traces import ROOT

IMAGE = "python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258"
NATIVE = ROOT / "native/target/release/kv-eviction-replay"


class PolicyError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class JsonLines:
    def __init__(self, stream):
        self.stream = stream
        self.pending = b""

    def read(self, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.stream, selectors.EVENT_READ)
            while b"\n" not in self.pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Timed out waiting for policy or replay")
                data = os.read(self.stream.fileno(), 65536)
                if not data:
                    raise RuntimeError(
                        "Policy/replay exited before completing; see stderr"
                    )
                self.pending += data
                if len(self.pending) > 8 * 1024 * 1024:
                    raise ValueError("Policy/replay response exceeds 8 MiB")
        line, self.pending = self.pending.split(b"\n", 1)
        message = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError(  # noqa: TRY004 - invalid wire data, not caller misuse
                "Policy/replay protocol requires a JSON object"
            )
        return message


def send_json(stream, value, timeout: float = 2) -> None:
    """Bound writes as well as reads: a hostile policy may stop reading stdin."""
    data = memoryview((json.dumps(value) + "\n").encode())
    deadline = time.monotonic() + timeout
    os.set_blocking(stream.fileno(), False)
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_WRITE)
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError("Timed out writing to policy or replay")
            try:
                written = os.write(stream.fileno(), data)
            except BlockingIOError:
                continue
            data = data[written:]


def stop(process: subprocess.Popen) -> None:
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


class PolicyProcess:
    def __init__(self, policy: Path, stderr, stack: ExitStack):
        self.name = f"kv-policy-{uuid.uuid4().hex}"
        command = [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--name",
            self.name,
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "128m",
            "--cpus",
            "1",
            "--pids-limit",
            "32",
            "--user",
            "65534:65534",
            "--mount",
            f"type=bind,src={policy.resolve()},dst=/submission/policy.py,readonly",
            "--mount",
            f"type=bind,src={ROOT / 'kv_experiment/policy_worker.py'},dst=/worker.py,readonly",
            IMAGE,
            "python",
            "-I",
            "-B",
            "-u",
            "/worker.py",
        ]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            start_new_session=True,
        )
        stack.callback(self.close)
        self.reader = JsonLines(self.process.stdout)
        ready = self.reader.read(30)
        if "error_kind" in ready:
            raise PolicyError(ready["error_kind"], ready["message"])
        if ready != {"status": "ready"}:
            raise ValueError("Policy failed startup handshake")

    def choose(self, candidates: list[dict]) -> dict:
        deadline = time.monotonic() + 2
        send_json(self.process.stdin, candidates)
        response = self.reader.read(max(0, deadline - time.monotonic()))
        if "error_kind" in response:
            raise PolicyError(response["error_kind"], response["message"])
        victim = response.get("victim_id")
        if type(victim) is not int or victim not in {
            row["block_id"] for row in candidates
        }:
            raise ValueError("Policy returned an invalid block ID")
        return {"victim_id": victim}

    def pause(self, pause: dict) -> dict:
        """Retention decision for a finished request: protect until a time, or not."""
        deadline = time.monotonic() + 2
        send_json(self.process.stdin, {"pause": pause})
        response = self.reader.read(max(0, deadline - time.monotonic()))
        if "error_kind" in response:
            raise PolicyError(response["error_kind"], response["message"])
        until = response.get("protect_until_ms")
        if until is not None and (
            type(until) not in (int, float) or not math.isfinite(until)
        ):
            raise ValueError("Policy returned an invalid protection time")
        return {"protect_until_ms": until}

    def close(self):
        subprocess.run(
            ["docker", "rm", "--force", self.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        stop(self.process)
        self.process.stdin.close()
        self.process.stdout.close()


def replay(
    trace: Path,
    config: Path,
    output: Path,
    policy: Path | None,
    ttft_slo_ms: float,
    itl_slo_ms: float,
    timeout: float = 600,
) -> dict:
    if not NATIVE.exists():
        raise FileNotFoundError(
            "Run python3 research/kv-eviction/prepare_native.py first"
        )
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    with ExitStack() as stack:
        policy_stderr = stack.enter_context((output / "policy-stderr.txt").open("wb"))
        worker = PolicyProcess(policy, policy_stderr, stack) if policy else None
        stderr = stack.enter_context((output / "native-stderr.txt").open("wb"))
        process = subprocess.Popen(
            [
                str(NATIVE),
                str(trace),
                str(config),
                str(output),
                "policy" if worker else "native-lru",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            start_new_session=True,
        )
        stack.callback(stop, process)
        stack.callback(process.stdin.close)
        stack.callback(process.stdout.close)
        reader = JsonLines(process.stdout)
        calls = 0
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Replay exceeded its wall-clock budget")
            event = reader.read(remaining)
            if event == {"status": "done"}:
                break
            if worker is None or set(event) not in ({"candidates"}, {"pause"}):
                raise ValueError("Unexpected replay event")
            response = (
                worker.choose(event["candidates"])
                if "candidates" in event
                else worker.pause(event["pause"])
            )
            send_json(process.stdin, response)
            calls += 1
        if process.wait(timeout=10) != 0:
            raise RuntimeError("Native replay failed")
    result = score(output, ttft_slo_ms, itl_slo_ms, write_request_scores=True)
    telemetry = json.loads(config.read_text()).get("telemetry")
    if telemetry:
        end_ms = max(
            json.loads(line)["terminal_time_ms"]
            for line in (output / "requests.jsonl").read_text().splitlines()
        )
        result["occupancy"] = summarize(
            read_samples(output / "occupancy.jsonl"),
            telemetry["sample_interval_ms"],
            end_ms,
        )
    result["policy_calls"] = calls
    result["simulator_wall_seconds"] = time.monotonic() - started
    (output / "metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
