"""
Synthetic trace generator that mimics Mooncake multi-turn conversation traces.

Each request is a dict:
    {
        "id":           int,
        "arrival_time": float,   # simulated seconds
        "input_tokens": list[int],
        "output_len":   int,
        "session_id":   int,
    }

Trace shape:
  - N sessions, each with a unique system prompt (shared prefix)
  - Each turn appends user tokens to the growing context
  - Sessions interleaved with Poisson arrivals
  - Some sessions repeat (simulating returning users) to reward smart policies

Call generate_trace() twice with different seeds to get independent
tuning and evaluation traces that share the same statistical shape
but have completely different token content.
"""

import random


BLOCK_SIZE = 16  # must match cache.py


def generate_trace(
    num_sessions: int = 40,
    turns_per_session: int = 6,
    system_prompt_tokens: int = 64,
    user_turn_tokens: int = 48,
    output_tokens: int = 64,
    repeat_fraction: float = 0.3,
    arrival_rate: float = 2.0,
    seed: int = 42,
) -> list[dict]:
    """
    Generate a synthetic multi-turn conversation trace.

    Use different seeds for tuning and evaluation so the two traces are
    statistically identical but have different token content — policies
    cannot overfit to the eval trace by memorizing tuning sessions.
    """
    rng = random.Random(seed)
    vocab_size = 32_000

    # One system prompt per session — the shared prefix each turn extends.
    system_prompts = [
        [rng.randint(0, vocab_size - 1) for _ in range(system_prompt_tokens)]
        for _ in range(num_sessions)
    ]

    # Build sessions: each turn appends user tokens to the growing context.
    sessions = []
    for s_idx in range(num_sessions):
        context = system_prompts[s_idx][:]
        session = []
        for _ in range(turns_per_session):
            user_tokens = [rng.randint(0, vocab_size - 1) for _ in range(user_turn_tokens)]
            context = context + user_tokens
            session.append({"input_tokens": list(context), "output_len": output_tokens})
        sessions.append(session)

    # Duplicate some sessions to simulate returning users.
    num_repeats = int(num_sessions * repeat_fraction)
    repeated_indices = rng.sample(range(num_sessions), min(num_repeats, num_sessions))
    for idx in repeated_indices:
        sessions.append(sessions[idx])

    # Flatten with Poisson arrival times; turns within a session are 1 s apart.
    inter_session_gap = 1.0 / arrival_rate
    flat = []
    for s_idx, session in enumerate(sessions):
        session_start = s_idx * inter_session_gap + rng.expovariate(arrival_rate)
        for t_idx, req in enumerate(session):
            flat.append({
                "arrival_time": session_start + t_idx * 1.0,
                "input_tokens": req["input_tokens"],
                "output_len":   req["output_len"],
                "session_id":   s_idx,
            })

    flat.sort(key=lambda r: r["arrival_time"])
    for i, req in enumerate(flat):
        req["id"] = i

    return flat
