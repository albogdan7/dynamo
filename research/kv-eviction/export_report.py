"""Build an auditable report from saved evidence, without calling an agent.

Standard library: HTML, Markdown, CSV, JSON and a self-contained evidence ZIP.
The HTML embeds the reproduction ZIP and needs no external assets or scripts.
"""

import argparse
import base64
import csv
import hashlib
import html
import io
import json
import math
import zipfile
from pathlib import Path

from summarize import METRICS, summarize

ROOT = Path(__file__).resolve().parent
TRACES = ["mooncake-toolagent", "mooncake-conversation", "agentx", "freeinference"]
LABELS = {
    "mooncake-toolagent": "Mooncake tool and agent",
    "mooncake-conversation": "Mooncake conversation",
    "agentx": "AgentX subset",
    "freeinference": "FreeInference subset",
}
FIELDS = [
    ("mean_ttft_ms", "Mean TTFT", "ms", 3),
    ("p95_ttft_ms", "p95 TTFT", "ms", 3),
    ("mean_itl_ms", "Mean individual gap ITL", "ms", 5),
    ("p95_itl_ms", "p95 individual gap ITL", "ms", 5),
    ("goodput_rps", "SLO goodput", "requests/s", 11),
    ("slo_attainment_pct", "SLO attainment", "%", 5),
    ("token_hit_pct", "Input token hit rate", "%", 5),
    ("recomputed_input_tokens", "Recomputed input tokens", "tokens", 0),
    ("evictions", "Evictions", "512 token blocks", 0),
]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def evidence_path(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), "Evidence path escapes report root")
    return path


def validate(root, raw_root=None):
    """Verify sources, policies, selection and receipts before reporting scores."""
    runs = {}
    files = {}
    audit_count = 0
    raw_count = 0
    for filename in sorted((root / "evidence/comparison/runs").glob("*.json")):
        record = read(filename)
        key = (record["trace"], record["agent"])
        require(key not in runs, "Duplicate trace and agent record")
        runs[key] = record
        files[str(filename.relative_to(root))] = sha(filename)
        manifest = record["manifest"]
        snapshot = evidence_path(root, record["source_snapshot_path"])
        require(sha(snapshot) == record["source_snapshot_sha256"], "Source ZIP changed")
        files[str(snapshot.relative_to(root))] = sha(snapshot)
        with zipfile.ZipFile(snapshot) as archive:
            for name, expected in manifest["source"]["files"].items():
                actual = hashlib.sha256(archive.read(name)).hexdigest()
                require(actual == expected, f"Source snapshot mismatch: {name}")
        bundle = snapshot.parent
        receipt = read(bundle / "replay-check.json")
        require(receipt["matches"] and all(receipt["fields"].values()), "Failed replay")
        audits = read(bundle / "independent-audit.json")
        require(len(audits) == record["audited_replays"], "Audit count mismatch")
        require(
            all(item["matches"] for item in audits.values()), "Failed arithmetic audit"
        )
        audit_count += len(audits)
        statuses = []
        for submission in record["submissions"]:
            status = submission["status"]
            require(
                status["valid"],
                "Report requires explicit handling of invalid submissions",
            )
            policy = evidence_path(root, submission["policy_path"])
            require(sha(policy) == status["policy_sha256"], f"Policy changed: {policy}")
            files[str(policy.relative_to(root))] = sha(policy)
            statuses.append(status)
        require(
            len(statuses) == manifest["submission_budget"], "Submission count mismatch"
        )
        ordered = sorted(statuses, key=lambda item: item["ordinal"])
        selected = max(
            ordered,
            key=lambda item: (
                item["tuning"]["goodput_rps"],
                -item["tuning"]["mean_ttft_ms"],
            ),
        )
        require(
            selected["id"] == record["selection"]["submission_id"],
            "Selection rule mismatch",
        )
        require(
            selected["policy_sha256"] == record["selection"]["policy_sha256"],
            "Selected source mismatch",
        )
        require(
            selected["tuning"] == record["selection"]["tuning"],
            "Selected tuning score mismatch",
        )
        for split in ("tuning", "evaluation"):
            baseline = record["baselines"]["native-lru"][split]
            leaf = record["baselines"]["leaf-first-lru"][split]
            require(
                all(baseline[field] == leaf[field] for field in METRICS),
                "Native leaf first baseline differs",
            )
        for metrics in [record["evaluation"], *(s["tuning"] for s in statuses)]:
            expected = metrics["slo_passes"] * 1000 / metrics["observation_duration_ms"]
            require(
                math.isclose(metrics["goodput_rps"], expected, rel_tol=1e-12),
                "Goodput arithmetic mismatch",
            )
        for name in ("replay-check.json", "independent-audit.json"):
            files[str((bundle / name).relative_to(root))] = sha(bundle / name)
        if raw_root is not None:
            from evidence.audit_run import audit

            directory = raw_root / Path(record["original_run"]).name
            raw_manifest = read(directory / "manifest.json")
            require(raw_manifest == manifest, "Raw and compact manifests differ")
            require(
                sha(directory / "config.json") == manifest["config_sha256"],
                "Raw configuration changed",
            )
            for split in ("tuning", "evaluation"):
                require(
                    sha(directory / f"{split}-trace.jsonl")
                    == manifest["trace"]["windows"][split]["sha256"],
                    "Raw trace changed",
                )
            for relative, saved in audits.items():
                actual = audit(directory / relative)
                require(
                    actual == saved,
                    f"Raw arithmetic or metric file changed: {directory / relative}",
                )
                metrics = read(directory / relative / "metrics.json")
                if relative == "evaluation":
                    expected_metrics = record["evaluation"]
                elif relative.startswith("submissions/"):
                    identity = Path(relative).parts[1]
                    expected_metrics = next(
                        s["tuning"] for s in statuses if s["id"] == identity
                    )
                else:
                    baseline_name, split = relative.rsplit("-", 1)
                    expected_metrics = record["baselines"][baseline_name][split]
                require(metrics == expected_metrics, "Raw and compact metrics differ")
                raw_count += 1
    require(
        set(runs)
        == {(trace, agent) for trace in TRACES for agent in ("claude", "codex")},
        "Expected four paired traces",
    )
    for name in (
        "legacy-reproduction.json",
        "comparison/toolagent-baseline-wrapper-compatibility.json",
    ):
        path = root / "evidence" / name
        files[str(path.relative_to(root))] = sha(path)
    historical = root / "evidence/mooncake-claude-pilot"
    for path in [
        *(historical / name for name in ("baselines.json", "evaluation.json")),
        *historical.glob("submissions/*/submission.json"),
    ]:
        files[str(path.relative_to(root))] = sha(path)
    return runs, {
        "input_sha256": files,
        "saved_audit_receipts": audit_count,
        "raw_replays_reaudited": raw_count,
        "verified_policy_sources": sum(len(r["submissions"]) for r in runs.values()),
        "verified_source_snapshots": len(runs),
        "selected_replay_receipts": len(runs),
    }


def fmt(value, decimals=3):
    return "unavailable" if value is None else f"{value:,.{decimals}f}"


def delta(value, baseline):
    return 100 * (value / baseline - 1) if baseline else None


def usage(record):
    results = {
        key: 0 for key in ("input", "cache_read", "cache_write", "output", "seconds")
    }
    costs = []
    for submission in record["submissions"]:
        metadata = submission["agent_metadata"]
        tokens = metadata["usage"]
        results["input"] += tokens["input_tokens"]
        results["cache_read"] += tokens.get(
            "cache_read_input_tokens", tokens.get("cached_input_tokens", 0)
        )
        results["cache_write"] += tokens.get(
            "cache_creation_input_tokens", tokens.get("cache_write_input_tokens", 0)
        )
        results["output"] += tokens["output_tokens"]
        results["seconds"] += metadata["runtime_seconds"]
        costs.append(metadata.get("api_equivalent_usd"))
    results["usd"] = sum(costs) if all(c is not None for c in costs) else None
    return results


def sections(root, runs, validation):
    pages = []
    blocks = []

    def page(title):
        nonlocal blocks
        blocks = []
        pages.append((title, blocks))

    def p(text):
        blocks.append(("p", text))

    def h(text):
        blocks.append(("h", text))

    def table(headers, rows, widths=None):
        blocks.append(("table", (headers, rows, widths)))

    def code(text):
        blocks.append(("code", text))

    count = validation["verified_policy_sources"]
    dates = [r["manifest"]["created_at"][:10] for r in runs.values()]
    page("KV eviction experiment results")
    p(
        f"Results recorded {min(dates)} to {max(dates)} in America Chicago time. Repository albogdan7/dynamo. The comparison covers {len(runs)} agent searches, {count} valid scored policy submissions, four workloads and one model per agent backend."
    )
    p(
        "The agents can generate and submit executable eviction policies, and the saved scores reproduce. The small improvement on Mooncake tool and agent does not extend to a clear gain on the added workloads. Neither agent establishes a meaningful goodput advantage or a reliable model ranking."
    )
    summary_rows = []
    for trace in TRACES:
        left, right = runs[trace, "claude"], runs[trace, "codex"]
        baseline = left["baselines"]["native-lru"]["evaluation"]
        summary_rows.append(
            [
                LABELS[trace],
                fmt(baseline["mean_ttft_ms"], 2),
                fmt(left["evaluation"]["mean_ttft_ms"], 2),
                fmt(right["evaluation"]["mean_ttft_ms"], 2),
                f"{baseline['slo_passes']}/{baseline['slo_eligible_requests']}",
            ]
        )
    table(
        [
            "Held out workload",
            "LRU TTFT ms",
            "Claude TTFT ms",
            "Codex TTFT ms",
            "SLO passes each",
        ],
        summary_rows,
        [2.1, 1.05, 1.05, 1.05, 1.05],
    )
    tool = runs["mooncake-toolagent", "codex"]
    baseline = tool["baselines"]["native-lru"]["evaluation"]
    p(
        f"On tool and agent, both selected policies reduce mean TTFT by {fmt(-delta(tool['evaluation']['mean_ttft_ms'], baseline['mean_ttft_ms']), 2)}%. Goodput increases only {fmt(delta(tool['evaluation']['goodput_rps'], baseline['goodput_rps']), 5)}%, because the drain duration shortens; the passing request count is unchanged. Every selected policy has unchanged p95 TTFT and p95 ITL versus its own LRU baseline."
    )
    h("How to interpret the result")
    p(
        "All timings are estimates from an uncalibrated polynomial simulator. They are not measured GPU performance. Each trace has different arrival patterns and, for session traces, different cache capacities. Compare a policy with its baseline within the same workload; do not rank absolute goodput across traces."
    )
    p(
        "AgentX is a small qualified subset with synthetic serialization between plays. FreeInference excludes ambiguous zero output counts and unsupported nested or overlapping sessions; all evaluation requests pass its SLO. These choices limit generalization and the ability to distinguish policies."
    )
    p(
        "The following pages define the metrics and workloads, show every tuning submission and held out metric, separate the historical controls, and provide scripts and commands for retrieval and reproduction. Exact numerical values remain available in CSV and JSON without display rounding."
    )

    page("Method and individual metrics")
    p(
        "The controller starts from LRU, invokes a real coding agent five times, and evaluates each saved policy on tuning data. It selects highest tuning goodput, then lowest mean tuning TTFT, then the earliest submission. Held out evaluation starts with a fresh policy instance, cache and scheduler. There is no warmup. No evaluation feedback is returned to the agent."
    )
    rows = [
        [
            "Mean and p95 TTFT",
            "Time from request readiness or arrival through queueing to its first output token. Completed positive output requests only. Lower is better.",
        ],
        [
            "Mean and p95 ITL",
            "Individual consecutive output token gaps pooled over completed requests. Long outputs contribute more gaps. These are not average time per output token. Lower is better.",
        ],
        [
            "SLO goodput",
            "Passing eligible requests divided by simulated first arrival to last terminal duration in seconds. Includes queue drain and human or tool idle time. Higher is better.",
        ],
        [
            "SLO attainment",
            "Passing requests divided by all positive output eligible requests, including failed eligible requests in the denominator. Higher is better.",
        ],
        [
            "Input token hit rate",
            "Input tokens reused at first admission divided by all input tokens. Higher generally indicates more reuse.",
        ],
        [
            "Recomputed input tokens",
            "Committed prefill tokens, including repeated prefill after preemption. This need not equal input tokens minus first admission reuse. Lower is better.",
        ],
        [
            "Evictions",
            "Number of 512 token blocks evicted. Diagnostic rather than a stand alone quality objective.",
        ],
    ]
    table(["Metric", "Definition"], rows, [1.7, 4.7])
    p(
        "SLO fixed before the matched searches: TTFT at most 1000 ms AND each request's p95 individual token gap ITL at most 50 ms. All p95 values use nearest rank. A one token output has no ITL test; zero output requests are excluded from SLO eligibility. All matched evaluation requests completed and had positive output counts."
    )
    h("Fixed serving and policy contract")
    profile = runs["agentx", "codex"]["manifest"]["timing"]["rank"]
    p(
        f"One native vLLM worker, {profile['block_size']} token engine blocks, at most {profile['max_num_seqs']} active sequences and {profile['max_num_batched_tokens']} batched tokens. Timing model is {profile['timing_model']['type']}; no offload or speculation. Cache capacity and arrival scaling are frozen per workload."
    )
    p(
        "Policies choose one inactive, unreferenced, unpinned candidate block. They cannot change scheduling, capacity, timing, traces or scoring. Candidate fields describe physical allocation and release counters and native LRU rank. Release count is not an exact hit count or prefix depth. Policy Python and IPC wall time do not advance simulated time."
    )

    explanations = {
        "mooncake-toolagent": (
            "Claude minimizes release count then LRU rank. Codex gives copies released more than once a protected tier, retaining LRU within each tier. Different source programs produce identical measured scores on this cohort; this does not establish universal equivalence.",
            "The passing count and tail latencies do not improve. The small mean latency and reuse gain alone does not establish a useful serving or model advantage.",
        ),
        "mooncake-conversation": (
            "Claude caps release count at two before applying LRU. Codex applies a bounded release count bonus against logical release age. Both selected sources differ from plain LRU, but all measured evaluation metrics match LRU on this cohort.",
            "Low cache reuse and substantial queueing dominate this cohort. Identical pilot scores establish no policy or agent winner.",
        ),
        "agentx": (
            "Both agents selected a policy that returns the smallest native LRU rank. The table lists the tuning scores of all submitted policies; selection follows tuning goodput and mean TTFT only.",
            "The selected policies reproduce LRU, so identical held out scores are expected. Tuning SLO attainment is nearly saturated. Four complete plays cannot establish performance on the published corpus.",
        ),
        "freeinference": (
            "Claude searches the older half of the queue, then ranks release count and creation order. Codex applies a binary protected tier for copies released more than once. Neither policy was selected using held out performance.",
            "All held out requests pass the SLO, so it cannot discriminate passing counts. Codex causes slightly more recomputation and evictions and worsens mean TTFT. The subset excludes sessions with unresolved output or dependency semantics.",
        ),
    }
    for trace in TRACES:
        page(LABELS[trace])
        left, right = runs[trace, "claude"], runs[trace, "codex"]
        manifest = left["manifest"]
        provenance = manifest["trace"]
        tuning, evaluation = (
            provenance["windows"][s] for s in ("tuning", "evaluation")
        )
        cache = manifest["timing"]["rank"]["num_gpu_blocks"] * 512
        cohort = f"Tuning {tuning['requests']} requests; evaluation {evaluation['requests']} requests. Cache {cache:,} tokens. Arrival or delay multiplier {provenance['arrival_time_multiplier']:g}. "
        if "source_ordinals" in tuning:
            cohort += f"Complete session source ordinals {tuning['source_ordinals']} for tuning and {evaluation['source_ordinals']} for evaluation. "
        else:
            cohort += f"Source row intervals [{tuning['start_row']}, {tuning['start_row'] + tuning['requests']}) and [{evaluation['start_row']}, {evaluation['start_row'] + evaluation['requests']}). "
        p(cohort)
        h("Held out performance")
        baseline = left["baselines"]["native-lru"]["evaluation"]
        metrics = [baseline, left["evaluation"], right["evaluation"]]
        table(
            ["Metric and unit", "LRU", "Selected Claude", "Selected Codex"],
            [
                [f"{label} ({unit})", *(fmt(m[field], digits) for m in metrics)]
                for field, label, unit, digits in FIELDS
            ]
            + [
                [
                    "SLO passes / eligible",
                    *(
                        f"{m['slo_passes']} / {m['slo_eligible_requests']}"
                        for m in metrics
                    ),
                ],
                [
                    "Observation span (seconds)",
                    *(fmt(m["observation_duration_ms"] / 1000, 3) for m in metrics),
                ],
            ],
            [2.55, 1.28, 1.28, 1.28],
        )
        h("Every submitted policy on tuning data")
        submission_rows = []
        for agent in ("claude", "codex"):
            record = runs[trace, agent]
            for sub in sorted(
                record["submissions"], key=lambda item: item["status"]["ordinal"]
            ):
                status = sub["status"]
                m = status["tuning"]
                marker = (
                    " selected"
                    if sub["id"] == record["selection"]["submission_id"]
                    else ""
                )
                submission_rows.append(
                    [
                        f"{agent.title()} {status['ordinal']}{marker}",
                        fmt(m["goodput_rps"], 9),
                        fmt(m["mean_ttft_ms"], 3),
                        f"{m['slo_passes']}/{m['slo_eligible_requests']}",
                        fmt(m["evictions"], 0),
                    ]
                )
        table(
            ["Submission", "Goodput req/s", "Mean TTFT ms", "SLO passes", "Evictions"],
            submission_rows,
            [1.8, 1.4, 1.2, 1.0, 1.0],
        )
        p(explanations[trace][0])
        p(explanations[trace][1])

    page("Agent execution and usage")
    p(
        "Claude Code uses model claude-sonnet-5-5, reported by its CLI. Codex requests gpt-6.1-sol with medium reasoning effort; its CLI does not expose the server resolved model identity. Tested CLIs are Claude Code 2.1.285 and Codex 0.164.0. This is one fixed budget search per model per workload, with no repeated model seeds or statistical confidence interval."
    )
    p(
        "Claude uses restricted file tools. Codex receives the current policy in its prompt and must successfully edit policy.py through its patch tool. Shell, network, apps, plugins and subagent tools are disabled. Both receive tuning feedback. The tool surface differs, so these runs are not a controlled study of model quality alone."
    )
    rows, costs = [], []
    for trace in TRACES:
        for agent in ("claude", "codex"):
            r = runs[trace, agent]
            u = usage(r)
            label = f"{trace.removeprefix('mooncake-')} {agent}"
            rows.append(
                [
                    label,
                    *(
                        fmt(u[k], 0)
                        for k in ("input", "cache_read", "cache_write", "output")
                    ),
                ]
            )
            costs.append(
                [
                    label,
                    fmt(u["seconds"], 2),
                    fmt(u["usd"], 6),
                    str(len(r["submissions"])),
                ]
            )
    h("CLI reported token totals across all five invocations")
    table(
        ["Workload and agent", "Input reported", "Cache read", "Cache write", "Output"],
        rows,
        [2.15, 1.05, 1.05, 1.05, 1.05],
    )
    p(
        "The CLI token schemas differ. Codex input includes its cached input subset; Claude reports noncached input and cache read and write counts separately. Do not add input and cached columns indiscriminately or infer a direct efficiency ranking."
    )
    h("Agent runtime and reported cost")
    table(
        ["Workload and agent", "Agent seconds", "API equivalent USD", "Valid policies"],
        costs,
        [2.2, 1.25, 1.6, 1.2],
    )
    p(
        "Runtime is the sum of agent invocation wall times, excluding replay. Claude cost is a CLI reported API equivalent estimate, not subscription billing. Codex monetary cost is unavailable and remains null; it is not zero. All matched searches scored five valid policies. Earlier failed setup probes are excluded from these totals and never received a performance score."
    )

    page("Historical baseline and overload control")
    legacy = read(root / "evidence/legacy-reproduction.json")
    p(
        f"The baseline was reproduced from pinned source commit {legacy['baseline_commit']}. These legacy scores use a cache proxy, 50 ms plus 3 ms per missed block; they cannot measure ITL or achieved serving goodput. They must not be compared numerically with native timing."
    )
    policies = [legacy["results"][key]["eval"] for key in ("lru", "leaf-first-lru")]
    table(
        ["Legacy evaluation", "LRU", "Leaf first LRU"],
        [
            ["Mean proxy TTFT (ms)", *(fmt(m["mean_ttft_ms"], 2) for m in policies)],
            [
                "Input block hit rate (%)",
                *(fmt(100 * m["overall_hit_rate"], 2) for m in policies),
            ],
            ["Evictions", *(fmt(m["evictions"], 0) for m in policies)],
            ["Requests", *(fmt(m["num_requests"], 0) for m in policies)],
        ],
        [3.0, 1.7, 1.7],
    )
    p(
        "The earlier screenshot mixed LRU tuning numbers with leaf first evaluation numbers. The table uses evaluation against evaluation. In native serving, release order is unique; the creation order tiebreak yields the same metrics as native LRU. Every matched run verifies both native baselines agree on all nine reported metrics."
    )
    pilot = root / "evidence/mooncake-claude-pilot"
    baseline = read(pilot / "baselines.json")["native-lru"]["evaluation"]
    selected = read(pilot / "evaluation.json")
    statuses = [
        read(path) for path in (pilot / "submissions").glob("*/submission.json")
    ]
    failed = [s for s in statuses if not s["valid"]]
    h("Original arrival rate control")
    p(
        f"A separate claude-sonnet-4-6 search used 1000 requests per split at original arrivals. Of {len(statuses)} attempts, {len(statuses) - len(failed)} were valid and {len(failed)} hit an agent timeout. The timeout was retained without a fallback score. This overloaded configuration is not comparable with the smaller, slower arrival matched searches."
    )
    table(
        ["Native evaluation metric", "LRU", "Selected policy"],
        [
            [
                f"{label} ({unit})",
                fmt(baseline[field], digits),
                fmt(selected[field], digits),
            ]
            for field, label, unit, digits in FIELDS
        ],
        [3.0, 1.7, 1.7],
    )
    p(
        f"Only {baseline['slo_passes']}/{baseline['requests']} requests pass for LRU and {selected['slo_passes']}/{selected['requests']} for the selected policy. This control establishes overload rather than a useful model ranking. Historical smoke checks and setup probes are not additional scored searches in the four paired comparisons."
    )

    page("Trace selection and serving semantics")
    h("Mooncake")
    p(
        "The complete source releases contain 23608 tool and agent requests and 12031 conversation requests. They provide millisecond arrivals and 512 token prefix hashes. Tuning uses the start of the source; evaluation uses its midpoint. The matched pilots use 256 requests per split and explicitly multiply interarrival timing by ten. Contiguous windows do not prove session independence."
    )
    h("AgentX")
    p(
        "Download only the first 16 complete plays, bounded to 128 MiB. A play qualifies if it contains a subagent group, has at most 128 model requests, has strictly positive outputs and fits the 196608 token request bound. Source ordinals 2 and 7 tune; 10 and 13 evaluate. All requests of each selected play are retained. The full published corpus is 393 plays and 98827 requests; these results are not that benchmark."
    )
    p(
        "Source identities describe 64 token blocks and have documented input counting artifacts. The native Weka importer supplies completion, spawn and join edges. It selected an absolute nested timestamp interpretation, recorded in the import report. Descendant authored time floors are removed, preserving causal completion and tool delays. Top level plays execute serially because cross play arrival times are unavailable."
    )
    h("FreeInference")
    p(
        "Download the complete 2026-05-20 day, 17 root sessions and 383 total model requests. There are 101 explicitly zero reported completion counts, some alongside tool calls; their true generated lengths are ambiguous. Exclude affected whole sessions, nested subagents and overlapping request streams. Seven sequential positive output sessions qualify; the pilot uses ordinals 0, 1 and 3 for tuning and 4, 13 and 14 for evaluation. The remaining eligible session is unused."
    )
    p(
        "Inputs use o200k_base role token sums rather than provider prompt counts; outputs use provider completion counts. Complete 16 token identities are retained. Any unknown partial input tail receives a unique private identity. Root arrivals preserve the observed global clock; each successor becomes ready on simulated predecessor completion plus the observed next start minus prior end gap."
    )
    h("Common lowering and limits")
    p(
        "Session adapters write the existing dynamo.agentic_mooncake version 2 contract. The native loader coarsens 16 or 64 token identities into 512 token engine pages. KV identities are isolated by model and, for FreeInference, provider. Real model weights and model specific service speeds are not represented by the generic timing worker. Full nested FreeInference replay is unsupported. TraceLab and GAIATrace remain unvalidated candidates."
    )

    page("Verification and evidence boundaries")
    p(
        f"Report generation verified {validation['verified_policy_sources']} policy source hashes, {validation['verified_source_snapshots']} exact controller source ZIPs, {validation['selected_replay_receipts']} successful selected policy replay receipts and {validation['saved_audit_receipts']} saved independent arithmetic audit receipts. It recomputed each selection from submitted tuning scores and checked matched workload, configuration, SLO, budget, selection and baseline metrics before emitting comparison tables."
    )
    p(
        f"This export additionally reaudited {validation['raw_replays_reaudited']} raw replay directories from saved request and token timestamps."
        if validation["raw_replays_reaudited"]
        else "This export used committed audit receipts. It did not independently recompute raw token timing arithmetic; rerun with --raw-root to perform that stronger check when the raw files are available."
    )
    p(
        "The independent audit checks per token event counts, pooled token gaps, nearest rank percentiles, SLO decisions, observation duration, first admission reuse, recomputation and eviction counts, and reconciles mean timings with the native collector. Selected policy fresh replay receipts check all saved performance fields. Tool and agent replays also passed with the updated wrapper; those receipts record the original and rebuilt binary hashes where available."
    )
    h("Behavioral validation")
    p(
        "The completed implementation test run passed 25 tests, including actual Codex filesystem, network and configuration injection denial; actual Docker policy isolation, invalid decisions and timeouts; native completion and tool gap clocks, spawn and join; and 16 to 512 token prefix accounting. Report generation does not rerun those runtime tests. The reproduction page includes the command for doing so."
    )
    h("What the saved evidence can establish")
    p(
        "Compact evidence contains all submitted sources, scores, agent usage and manifests, controller source snapshots, arithmetic receipts and selected replay receipts. It supports transparent table generation and source integrity checks without API credentials or a simulator build. Hashes establish consistency with recorded files; they are not independent attestation of experimental conduct."
    )
    p(
        "Full raw request and token timestamps, transcripts, selected trace rows and compressed run archives remain local in the ignored experiments directory. They are needed to independently redo arithmetic and to replay a recorded run directly. The shareable compact bundle does not include them. The source releases are public and pinned, so a fresh checkout can fetch the same data and rerun experiments."
    )
    p(
        "Agent policy generation is nondeterministic: rerunning a model can generate another source program. Numerical reproduction means replaying the saved policy with matching data, configuration and compatible native build. A native binary hash can change across toolchains; the default rejects it, while an explicit rebuild check still requires the saved performance to match."
    )

    page("Regenerate and audit the report")
    p(
        "All commands below run from the repository root. The downloaded report bundle preserves this same relative layout. HTML, Markdown, CSV, JSON and evidence checking need only Python 3.12 or newer; they do not call an agent, use a GPU or require Docker."
    )
    code(
        "python3 research/kv-eviction/export_report.py\n  --output research/kv-eviction/reports".replace(
            ".py\n  ", ".py \\\n  "
        )
    )
    p(
        "The HTML is one self-contained attachment with inline styles and no JavaScript, external fonts or network requests. Its Download reproduction files link contains the generator, all compact evidence and the 40 submitted policy sources. Extract that ZIP and run the command above to regenerate the numerical report without an account or model call."
    )
    p(
        "To recompute raw timing arithmetic for all matched searches, point to the directory containing their original full run folders. Missing or changed raw files cause failure; this option does not silently fall back to receipts."
    )
    code(
        "python3 research/kv-eviction/export_report.py \\\n  --raw-root research/kv-eviction/experiments \\\n  --output /tmp/kv-eviction-audited-report"
    )
    p(
        "For one saved run, these commands perform independent arithmetic and a fresh selected policy replay. Replay requires the original run directory, Docker and the built native adapter. Use a fresh output name each time."
    )
    code(
        "python3 research/kv-eviction/evidence/audit_run.py \\\n  research/kv-eviction/experiments/extension-codex-agentx\npython3 research/kv-eviction/experiment.py evaluate \\\n  research/kv-eviction/experiments/extension-codex-agentx \\\n  --output experiments/my-agentx-replay"
    )
    p(
        "If the native build differs, evaluate requires explicit --allow-native-rebuild and records both hashes while still requiring all saved performance fields to match. This is compatibility validation, not byte identical build reproduction."
    )
    h("Report files")
    code(
        "reports/\n  kv-eviction-results.html   # Offline readable and printable\n  kv-eviction-results.md     # Generated narrative and tables\n  results.csv               # All held out metrics\n  submissions.csv           # All 40 tuning scores and policy IDs\n  agent-usage.csv            # Runtime and raw usage totals\n  report-manifest.json      # Input and output checksums\n  reproduction.zip          # Scripts and compact evidence embedded in HTML"
    )

    page("Run the experiments again")
    p(
        "Use the fork branch that contains all prerequisites. Native setup requires Rust and Cargo, policy execution requires Docker, and the selected agent CLI must be installed and signed in. FreeInference fetching requires zstd. Use an account supported model ID. Real agent invocations consume the account's allowance. No GPU is required."
    )
    code(
        "git clone https://github.com/albogdan7/dynamo.git\ncd dynamo\ngit checkout research/kv-eviction-additional-traces\npython3 research/kv-eviction/prepare_native.py"
    )
    p(
        "Each command below is generated from the recorded run manifest, with portable paths and a fresh output name. It retains the actual model, data, capacity, arrival settings and submission budget. The Claude tool and agent run was generated earlier; the other seven searches add 35 scored policies."
    )
    for trace in TRACES:
        if trace == "agentx":
            page("Run complete session experiments")
            p(
                "These commands retain the complete qualified sessions, cache capacities, models and submission budgets from the recorded manifests. They use the same setup as the Mooncake experiments. Interarrival and tool delay scaling remains one."
            )
        h(LABELS[trace])
        for agent in ("claude", "codex"):
            record = runs[trace, agent]
            command = record["manifest"]["command"]
            arguments = command[2:]
            arguments = list(arguments)
            index = arguments.index("--output")
            arguments[index + 1] = f"experiments/reproduce-{trace}-{agent}"
            # Keep one option per line so it can be inspected and modified.
            parts = ["python3 research/kv-eviction/experiment.py " + arguments[0]]
            parts.extend(
                "  " + " ".join(arguments[i : i + 2])
                for i in range(1, len(arguments), 2)
            )
            code(" \\\n".join(parts))
    p(
        "The runner refuses to overwrite output folders. Failed submissions are retained without successful fallback scores. Changing a source, window, SLO or cache budget creates a new experiment and must not be pooled with these paired results."
    )

    page("Retrieve outputs and validate the implementation")
    p(
        "A raw run retains everything needed to distinguish an agent invocation, its proposed source, tuning feedback, selection and held out performance. Submission IDs use date and Chicago wall clock time with an offset in metadata and a collision suffix when needed."
    )
    code(
        "experiments/reproduce-agentx-codex/\n  manifest.json             # CLI, model, source and data hashes\n  controller-source.zip     # Exact research sources at setup\n  config.json\n  tuning-source.jsonl       # Complete selected source sessions\n  evaluation-source.jsonl\n  tuning-trace.jsonl        # Canonical lowered replay input\n  evaluation-trace.jsonl\n  baselines.json\n  submissions/YYYY-MM-DD-HH-MM-SS/\n    policy.py\n    submission.json\n    prompt.md\n    agent-command.json\n    agent.jsonl\n    agent-metadata.json\n    tuning/                 # Metrics, requests, tokens, evictions\n  selection.json\n  selected-policy.py\n  evaluation.json\n  evaluation/               # Same raw accounting files\n  comparisons.json\n  summary.md"
    )
    p(
        "Export a full run while excluding its mutable agent workspace and copied runtime. Keep trace attribution with redistributed trace material. Source and policy hashes remain in the export."
    )
    code(
        "tar --exclude='*/agent-workspace' -czf my-agentx-run.tar.gz \\\n  -C research/kv-eviction/experiments reproduce-agentx-codex"
    )
    p(
        "To reproduce the implementation checks, create the standalone test environment, build the native adapter and enable the real runtime boundaries. Native tests otherwise skip if the binary is missing; Docker and Codex tests skip unless their flags are set. No model call is needed for permission boundary checks."
    )
    code(
        "uv venv --python 3.12 research/kv-eviction/.venv\nuv pip install --python research/kv-eviction/.venv/bin/python \\\n  pytest ruff\npython3 research/kv-eviction/prepare_native.py\nKV_RUN_CODEX_TESTS=1 KV_RUN_CONTAINER_TESTS=1 \\\n  research/kv-eviction/.venv/bin/python -m pytest -q \\\n  -c research/kv-eviction/pytest.ini research/kv-eviction/tests"
    )
    p(
        "For the original cache proxy controls, run python3 research/kv-eviction/experiment.py reproduce with a fresh --output experiments/my-baseline-reproduction. The pinned source object must be available in Git. This control remains separate from the native serving metrics."
    )

    page("Immutable sources and attribution")
    sources = {
        "mooncake-toolagent": "https://github.com/kvcache-ai/Mooncake/blob/{revision}/FAST25-release/README.md",
        "mooncake-conversation": "https://github.com/kvcache-ai/Mooncake/blob/{revision}/FAST25-release/README.md",
        "agentx": "https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/blob/{revision}/README.md",
        "freeinference": "https://huggingface.co/datasets/harvardMadsys/freeinference_agentic_trace/blob/{revision}/README.md",
    }
    for trace in TRACES:
        source = runs[trace, "claude"]["manifest"]["trace"]
        h(LABELS[trace])
        p(f"Revision {source['revision']}. License {source['license']}.")
        blocks.append(("hash", "Downloaded byte SHA256 " + source["sha256"]))
        if source.get("uncompressed_sha256"):
            blocks.append(
                ("hash", "Uncompressed SHA256 " + source["uncompressed_sha256"])
            )
        blocks.append(("link", sources[trace].format(revision=source["revision"])))
    p(
        "FreeInference attribution: William Nixon, Muxin Tian, Yunjia Zheng, Haryadi S. Gunawi and Juncheng Yang, From Requests to Sessions A Large Scale Characterization of Human Driven Agentic Workloads, 2026. Licensed CC BY 4.0. The filtered, model isolated replay is an adaptation; no source endorsement is implied. Mooncake and AgentX source repositories declare Apache 2.0."
    )
    p(
        "All full workload hashes, configuration hashes, native binary hashes, selected policy hashes, controller source file hashes, exact commands, CLI versions and usage records are preserved in evidence/comparison/runs and the bundled report manifest. A clean Git commit name alone is insufficient because the actual trial sources were snapshotted while implementation was in progress."
    )
    for number, name in (
        (2, "Baseline"),
        (3, "Initial Claude experiment"),
        (4, "Codex support"),
        (5, "Additional traces and results"),
    ):
        blocks.append(
            ("link", f"{name}: https://github.com/albogdan7/dynamo/pull/{number}")
        )
    p(
        "These links identify submitted changes, not a claim that the pull requests are merged. The report describes evidence recorded through 2026-10-29; it does not query live PR state."
    )
    return pages


def markdown(pages):
    lines = [
        "<!-- Generated by export_report.py from saved evidence. Do not edit. -->",
        "",
    ]
    for index, (title, blocks) in enumerate(pages):
        lines += [("# " if index == 0 else "## ") + title, ""]
        for kind, value in blocks:
            if kind == "table":
                headers, rows, _ = value
                lines += [
                    "| " + " | ".join(headers) + " |",
                    "| " + " | ".join("---" for _ in headers) + " |",
                ]
                lines += [
                    "| "
                    + " | ".join(str(cell).replace("|", "\\|") for cell in row)
                    + " |"
                    for row in rows
                ]
            elif kind == "code":
                lines += [
                    "```bash"
                    if not value.startswith(("reports/", "experiments/"))
                    else "```text",
                    value,
                    "```",
                ]
            else:
                lines.append(("### " if kind == "h" else "") + value)
            lines.append("")
    return "\n".join(lines)


def reproduction_archive(root):
    paths = {
        root / name
        for name in (
            "export_report.py",
            "summarize.py",
            "REPORT_EXPORT.md",
            "TRACE_SOURCES.md",
            "README.md",
            "evidence/audit_run.py",
            "evidence/legacy-reproduction.json",
        )
    }
    paths.update((root / "evidence/comparison").rglob("*"))
    paths.update((root / "evidence/mooncake-claude-pilot").rglob("*"))
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in paths if p.is_file()):
            info = zipfile.ZipInfo(
                str(Path("research/kv-eviction") / path.relative_to(root)),
                (2026, 10, 29, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    return content.getvalue()


def webpage(pages, reproduction):
    navigation = '<nav aria-label="Report contents"><details><summary>Report contents</summary><ol>'
    navigation += "".join(
        f'<li><a href="#section-{index}">{html.escape(title)}</a></li>'
        for index, (title, _) in enumerate(pages)
    )
    navigation += "</ol></details></nav>"
    payload = base64.b64encode(reproduction).decode("ascii")
    download = f'<p class="share">One file you can read offline or forward as an attachment. <a download="kv-eviction-reproduce.zip" href="data:application/zip;base64,{payload}">Download reproduction files</a> to rebuild the report with Python.</p>'
    items = []
    for index, (title, blocks) in enumerate(pages):
        items.append(
            f'<section id="section-{index}"><h{1 if index == 0 else 2}>{html.escape(title)}</h{1 if index == 0 else 2}>'
        )
        if index == 0:
            items.extend([download, navigation])
        for kind, value in blocks:
            if kind == "table":
                headers, rows, _ = value
                items.append(
                    '<div class="table-scroll" tabindex="0" role="region" aria-label="Scrollable results table"><table><thead><tr>'
                    + "".join(
                        f'<th scope="col">{html.escape(str(c))}</th>' for c in headers
                    )
                    + "</tr></thead><tbody>"
                )
                items.extend(
                    "<tr>"
                    + "".join(f"<td>{html.escape(str(c))}</td>" for c in row)
                    + "</tr>"
                    for row in rows
                )
                items.append("</tbody></table></div>")
            elif kind == "link":
                prefix, separator, url = value.partition("https://")
                items.append(
                    f'<p>{html.escape(prefix)}<a href="https://{html.escape(url, quote=True)}">https://{html.escape(url)}</a></p>'
                    if separator
                    else f"<p>{html.escape(value)}</p>"
                )
            else:
                tag = {"p": "p", "h": "h3", "code": "pre", "hash": "p"}[kind]
                items.append(f"<{tag}>{html.escape(value)}</{tag}>")
        items.append("</section>")
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"><title>KV eviction experiment results</title>
<style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{font:16px/1.6 system-ui,sans-serif;max-width:980px;margin:40px auto;padding:0 24px;color:#171717;background:white}h1{font-size:34px;line-height:1.2;margin:0 0 16px}h2{font-size:26px;line-height:1.25;margin:0 0 18px}h3{font-size:18px;margin:22px 0 10px}p{margin:0 0 16px}a{color:#17486b;text-underline-offset:3px}a:focus-visible,summary:focus-visible,.table-scroll:focus-visible{outline:3px solid #d29813;outline-offset:3px}nav{margin:20px 0 26px}summary{cursor:pointer;font-weight:600}nav ol{columns:2;column-gap:35px;padding-left:24px}nav li{break-inside:avoid;margin-bottom:5px}.share{font-size:14px;color:#444}.table-scroll{overflow-x:auto;max-width:100%;margin:18px 0 22px}table{border-collapse:collapse;width:100%;min-width:610px;font-size:14px;font-variant-numeric:tabular-nums}td,th{border:1px solid #d9d9d9;padding:8px 10px;vertical-align:middle}th{background:#25384a;color:white;text-align:left}tbody tr:nth-child(even){background:#f4f6f8}td:not(:first-child){text-align:right}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace;padding:10px 0;margin:0 0 18px}p,a{overflow-wrap:anywhere}section{margin-bottom:44px;scroll-margin-top:20px}section+section{border-top:1px solid #e2e5e8;padding-top:30px}@media(max-width:600px){body{font-size:15px;margin:24px auto;padding:0 16px}h1{font-size:28px}h2{font-size:23px}nav ol{columns:1}table{font-size:13px}pre{font-size:12px}}@media print{body{max-width:none;margin:0;font-size:10pt;padding:0}nav,.share{display:none}section{break-before:page;border:0!important;padding-top:0!important}section:first-child{break-before:auto}.table-scroll{overflow:visible}table{min-width:0;font-size:8.5pt}thead{display:table-header-group}tr,pre{break-inside:avoid}a{color:inherit;text-decoration:none}pre{font-size:8pt}}
</style></head><body><main>
"""
        + "\n".join(items)
        + "</main></body></html>\n"
    )


def write_csv(path, rows):
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build(root, output, raw_root=None):
    require(
        not output.resolve().is_relative_to((root / "evidence").resolve()),
        "Do not overwrite input evidence",
    )
    runs, verification = validate(root, raw_root)
    output.mkdir(parents=True, exist_ok=True)
    summarize(root / "evidence/comparison/runs", output)
    pages = sections(root, runs, verification)
    (output / "kv-eviction-results.md").write_text(markdown(pages))
    reproduction = reproduction_archive(root)
    (output / "reproduction.zip").write_bytes(reproduction)
    (output / "kv-eviction-results.html").write_text(webpage(pages, reproduction))
    submissions, usages = [], []
    for trace in TRACES:
        for agent in ("claude", "codex"):
            record = runs[trace, agent]
            usages.append(
                {
                    "trace": trace,
                    "agent": agent,
                    "model": record["model"],
                    **usage(record),
                }
            )
            for sub in record["submissions"]:
                status = sub["status"]
                submissions.append(
                    {
                        "trace": trace,
                        "agent": agent,
                        "id": sub["id"],
                        "ordinal": status["ordinal"],
                        "selected": sub["id"] == record["selection"]["submission_id"],
                        "policy_sha256": status["policy_sha256"],
                        "policy_path": sub["policy_path"],
                        **{field: status["tuning"][field] for field in METRICS},
                    }
                )
    write_csv(output / "submissions.csv", submissions)
    write_csv(output / "agent-usage.csv", usages)
    names = [
        "kv-eviction-results.md",
        "kv-eviction-results.html",
        "results.csv",
        "results.json",
        "submissions.csv",
        "agent-usage.csv",
        "reproduction.zip",
    ]
    verification["generator_sha256"] = sha(Path(__file__))
    verification["output_sha256"] = {name: sha(output / name) for name in names}
    (output / "report-manifest.json").write_text(
        json.dumps(verification, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "sections": len(pages),
                **{k: v for k, v in verification.items() if not k.endswith("sha256")},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports")
    parser.add_argument(
        "--raw-root",
        type=Path,
        help="Require and independently reaudit every original raw run",
    )
    args = parser.parse_args()
    build(ROOT, args.output, args.raw_root)
