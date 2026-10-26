"""
Synthetic trace generator that mimics Mooncake multi-turn conversation traces.

Each request is a dict:
    {
        "id":           int,
        "arrival_time": float,   # simulated seconds
        "input_tokens": list[int],
        "output_len":   int,
    }

Trace shape:
  - N sessions, each with a unique system prompt (shared prefix)
  - Each turn appends user tokens to the growing context
  - Sessions interleaved with Poisson arrivals
  - Some sessions repeat (simulating returning users) to reward smart policies
"""

import random
import math


BLOCK_SIZE = 16  # must match cache.py


def generate_trace(
    num_sessions: int = 40,
    turns_per_session: int = 6,
    system_prompt_tokens: int = 64,
    user_turn_tokens: int = 48,
    output_tokens: int = 64,
    repeat_fraction: float = 0.3,   # fraction of sessions that repeat (cache-friendly)
    arrival_rate: float = 2.0,      # requests per simulated second
    seed: int = 42,
) -> list[dict]:
    """
    Generate a synthetic multi-turn conversation trace.

    repeat_fraction controls how cache-friendly the workload is:
      0.0 = every session unique (LRU is fine)
      0.5 = half sessions repeat (smart policy wins big)
    """
    rng = random.Random(seed)

    # Build a pool of system prompts.
    # Tokens are small integers (vocabulary IDs). We use ranges that don't
    # overlap between prompts so hashing is deterministic.
    vocab_size = 32_000
    system_prompts = [
        [rng.randint(0, vocab_size - 1) for _ in range(system_prompt_tokens)]
        for _ in range(num_sessions)
    ]

    # Build sessions: each session is a list of requests (growing context).
    sessions = []
    for s_idx in range(num_sessions):
        context = system_prompts[s_idx][:]
        session = []
        for _ in range(turns_per_session):
            user_tokens = [rng.randint(0, vocab_size - 1) for _ in range(user_turn_tokens)]
            context = context + user_tokens
            session.append({
                "input_tokens": list(context),
                "output_len": output_tokens,
            })
        sessions.append(session)

    # Duplicate some sessions to simulate returning users.
    num_repeats = int(num_sessions * repeat_fraction)
    repeated_indices = rng.sample(range(num_sessions), min(num_repeats, num_sessions))
    for idx in repeated_indices:
        sessions.append(sessions[idx])

    # Flatten sessions into a request stream, interleaved with Poisson arrivals.
    # Each session fires turn-by-turn; sessions start at independent Poisson times.
    inter_session_gap = 1.0 / arrival_rate

    flat = []
    for s_idx, session in enumerate(sessions):
        session_start = s_idx * inter_session_gap + rng.expovariate(arrival_rate)
        for t_idx, req in enumerate(session):
            # Turns within a session are spaced 1s apart (tool-call pause analogue).
            flat.append({
                "arrival_time": session_start + t_idx * 1.0,
                "input_tokens": req["input_tokens"],
                "output_len":   req["output_len"],
                "session_id":   s_idx,   # used by split_trace
            })

    # Sort by arrival time and assign IDs.
    flat.sort(key=lambda r: r["arrival_time"])
    for i, req in enumerate(flat):
        req["id"] = i

    return flat


def split_trace(trace: list[dict], eval_fraction: float = 0.5, seed: int = 0):
    """
    Split into disjoint tuning and evaluation sets by SESSION, not by request
    index. This guarantees that no session has turns in both halves, so the
    eval set is truly held out — the agent cannot tune on early turns and get
    a free hit-rate boost on later turns of the same session.
    """
    session_ids = sorted({r["session_id"] for r in trace})
    rng = random.Random(seed)
    rng.shuffle(session_ids)

    n_eval = max(1, int(len(session_ids) * eval_fraction))
    eval_sessions  = set(session_ids[:n_eval])
    tuning_sessions = set(session_ids[n_eval:])

    tuning = sorted(
        [r for r in trace if r["session_id"] in tuning_sessions],
        key=lambda r: r["arrival_time"],
    )
    evaluation = sorted(
        [r for r in trace if r["session_id"] in eval_sessions],
        key=lambda r: r["arrival_time"],
    )
    return tuning, evaluation
