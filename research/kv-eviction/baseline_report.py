"""Offline HTML and CSV report for full-trace heuristic baselines.

Reads only audited compact evidence (never raw runs or simulations), verifies
every evidence checksum, and embeds the evidence plus this generator in the page.
"""

import base64
import csv
import hashlib
import html
import io
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABELS = {
    "mooncake-toolagent": "Mooncake tool and agent",
    "mooncake-conversation": "Mooncake conversation",
    "agentx": "AgentX",
    "freeinference": "FreeInference",
}
GENERATOR_FILES = [
    "baseline_report.py",
    "export_report.py",
    "baseline_comparison.py",
    "kv_experiment/baseline_evidence.py",
    "kv_experiment/comparison.py",
    "kv_experiment/inventory.py",
    "kv_experiment/releases.json",
    "evidence/audit_run.py",
    "configs/full-trace-baselines.json",
    "FULL_TRACE_BASELINE_SPEC.md",
    "FULL_TRACE_RESULTS.md",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def occupancy(series: str, key: str, period: str = "whole_run", scale: float = 1):
    def get(metrics):
        ranks = metrics.get("occupancy", {}).get("ranks", {})
        if len(ranks) != 1:
            return None
        (rank,) = ranks.values()
        value = rank[period][series][key]
        return None if value is None else value * scale

    return get


def field(name: str, scale: float = 1):
    def get(metrics):
        value = metrics.get(name)
        return None if value is None else value * scale

    return get


def span(name: str):
    return lambda metrics: metrics["occupancy"][name] / 1000


# One row per reported metric: (group, label, unit, value, decimals, better,
# change). `better` marks outcome metrics eligible for highlighting; `change` is
# relative % or percentage points versus LRU.
W, Q, P = "whole_run", "request_active", "points"
# fmt: off
ROWS = [
    ("Latency", "Mean TTFT, including queueing", "ms", field("mean_ttft_ms"), 0, "lower", "relative"),
    ("Latency", "p95 TTFT", "ms", field("p95_ttft_ms"), 0, "lower", "relative"),
    ("Latency", "Mean ITL over individual token gaps", "ms/gap", field("mean_itl_ms"), 3, "lower", "relative"),
    ("Latency", "p95 ITL over individual token gaps", "ms/gap", field("p95_itl_ms"), 3, "lower", "relative"),
    ("SLO", "SLO goodput", "passing requests/s", field("goodput_rps"), 6, "higher", "relative"),
    ("SLO", "SLO passes", "requests", field("slo_passes"), 0, "higher", "relative"),
    ("SLO", "SLO-eligible requests (positive output)", "requests", field("slo_eligible_requests"), 0, None, "relative"),
    ("SLO", "Zero-output requests (not SLO-eligible)", "requests", field("zero_output_requests"), 0, None, "relative"),
    ("SLO", "SLO attainment", "% of eligible", field("slo_attainment_pct"), 3, "higher", P),
    ("Cache effectiveness", "Input-token hit rate (first admission)", "%", field("token_hit_pct"), 3, "higher", P),
    ("Cache effectiveness", "Recomputed input tokens (incl. repeated prefill)", "tokens", field("recomputed_input_tokens"), 0, "lower", "relative"),
    ("Cache effectiveness", "Evicted blocks", "512-token blocks", field("evictions"), 0, "lower", "relative"),
    ("KV occupancy, whole run", "Physical (active + inactive), mean", "% of capacity", occupancy("physical_blocks", "mean_pct"), 2, None, P),
    ("KV occupancy, whole run", "Physical, time-weighted p95", "% of capacity", occupancy("physical_blocks", "p95_pct"), 2, None, P),
    ("KV occupancy, whole run", "Physical, sampled peak", "% of capacity", occupancy("physical_blocks", "peak_sampled_pct"), 2, None, P),
    ("KV occupancy, whole run", "Time with physical ≥ 95% of capacity", "% of time", occupancy("physical_blocks", "time_fraction_at_or_above_95pct", W, 100), 2, None, P),
    ("KV occupancy, whole run", "Time with physical at 100% of capacity", "% of time", occupancy("physical_blocks", "time_fraction_at_or_above_100pct", W, 100), 2, None, P),
    ("KV occupancy, whole run", "Active (referenced or reserved), mean", "% of capacity", occupancy("active_blocks", "mean_pct"), 2, None, P),
    ("KV occupancy, whole run", "Active, time-weighted p95", "% of capacity", occupancy("active_blocks", "p95_pct"), 2, None, P),
    ("KV occupancy, whole run", "Active, sampled peak", "% of capacity", occupancy("active_blocks", "peak_sampled_pct"), 2, None, P),
    ("KV occupancy, whole run", "Time with active ≥ 95% of capacity", "% of time", occupancy("active_blocks", "time_fraction_at_or_above_95pct", W, 100), 2, None, P),
    ("KV occupancy, whole run", "Time with active at 100% of capacity", "% of time", occupancy("active_blocks", "time_fraction_at_or_above_100pct", W, 100), 2, None, P),
    ("KV occupancy, whole run", "Inactive (reclaimable), mean", "% of capacity", occupancy("inactive_blocks", "mean_pct"), 2, None, P),
    ("KV occupancy, whole run", "Free, mean", "% of capacity", occupancy("free_blocks", "mean_pct"), 2, None, P),
    ("KV occupancy, request-active intervals", "Physical, mean", "% of capacity", occupancy("physical_blocks", "mean_pct", Q), 2, None, P),
    ("KV occupancy, request-active intervals", "Active, mean", "% of capacity", occupancy("active_blocks", "mean_pct", Q), 2, None, P),
    ("KV occupancy, request-active intervals", "Time with active ≥ 95% of capacity", "% of time", occupancy("active_blocks", "time_fraction_at_or_above_95pct", Q, 100), 2, None, P),
    ("Pressure and completion", "Preemptions", "events", lambda m: m["occupancy"]["preemptions"], 0, None, "relative"),
    ("Pressure and completion", "Waiting queue, time-weighted mean", "requests", occupancy("waiting_requests", "mean"), 1, None, "relative"),
    ("Pressure and completion", "Waiting queue, time-weighted p95", "requests", occupancy("waiting_requests", "p95"), 0, None, "relative"),
    ("Pressure and completion", "Waiting queue, sampled peak", "requests", occupancy("waiting_requests", "peak_sampled"), 0, None, "relative"),
    ("Pressure and completion", "Completed requests", "requests", field("completed"), 0, None, "relative"),
    ("Pressure and completion", "Failed or unfinished requests", "requests", lambda m: m["requests"] - m["completed"], 0, None, "relative"),
    ("Pressure and completion", "Observation span, first arrival to last terminal event", "s", field("observation_duration_ms", 1e-3), 1, None, "relative"),
    ("Pressure and completion", "Request-active span", "s", span("request_active_ms"), 1, None, "relative"),
]
# fmt: on


def change(value, reference, kind):
    if value is None or reference is None:
        return None
    if kind == "points":
        return value - reference
    return None if reference == 0 else 100 * (value / reference - 1)


def change_text(delta, kind) -> str:
    if delta is None:
        return "n/a vs LRU"
    if abs(delta) < 5e-13:
        return "= LRU"
    unit = " pp" if kind == "points" else "%"
    digits = 3 if abs(delta) >= 0.001 else 6
    return f"{delta:+,.{digits}f}{unit}"


def fmt(value, decimals) -> str:
    return "n/a" if value is None else f"{value:,.{decimals}f}"


def leaders(values, better, decimals):
    """Indices of the best displayed value; none when every value ties."""
    if better is None:
        return set()
    rounded = [None if v is None else round(v, decimals) for v in values]
    available = [v for v in rounded if v is not None]
    if len(set(available)) < 2:
        return set()
    best = (max if better == "higher" else min)(available)
    return {i for i, v in enumerate(rounded) if v == best}


def load(evidence: Path) -> dict:
    """Verify every compact evidence file against COMPLETE.json before use."""
    complete = json.loads((evidence / "COMPLETE.json").read_text())
    files = {
        str(path.relative_to(evidence))
        for path in evidence.rglob("*")
        if path.is_file() and path.name != "COMPLETE.json"
    }
    require(files == set(complete), "Evidence files differ from COMPLETE.json")
    for name, digest in complete.items():
        require(sha(evidence / name) == digest, f"Evidence file changed: {name}")
    results = json.loads((evidence / "results.json").read_text())
    for trace, item in results["traces"].items():
        if item["status"] == "ready":
            require(
                all(all(c.values()) for c in item["parity"].values()),
                f"Parity failed: {trace}",
            )
    return results


def tables(results: dict) -> list[dict]:
    """One table per ready trace: every heuristic as a column, LRU first."""
    sections = []
    policies = [
        name for name, item in results["policies"].items() if item["kind"] == "policy"
    ]
    for trace, item in results["traces"].items():
        if item["status"] != "ready":
            continue
        metrics = [item["policies"][name] for name in policies]
        rows = []
        for group, label, unit, get, decimals, better, kind in ROWS:
            values = [get(m) for m in metrics]
            rows.append(
                {
                    "group": group,
                    "metric": label,
                    "unit": unit,
                    "values": values,
                    "display": [fmt(v, decimals) for v in values],
                    "changes": [change(v, values[0], kind) for v in values],
                    "change_kind": kind,
                    "leaders": leaders(values, better, decimals),
                }
            )
        sections.append(
            {
                "trace": trace,
                "label": LABELS.get(trace, trace),
                "item": item,
                "rows": rows,
            }
        )
    return sections


def csv_rows(results: dict, sections: list[dict]) -> list[dict]:
    rows = []
    for section in sections:
        for row in section["rows"]:
            for index, policy in enumerate(
                name for name, p in results["policies"].items() if p["kind"] == "policy"
            ):
                rows.append(
                    {
                        "trace": section["trace"],
                        "policy": policy,
                        "group": row["group"],
                        "metric": row["metric"],
                        "unit": row["unit"],
                        "value": row["values"][index],
                        "change_vs_lru": row["changes"][index],
                        "change_kind": "percentage points"
                        if row["change_kind"] == "points"
                        else "relative %",
                    }
                )
    return rows


def coverage_table(results: dict) -> str:
    header = (
        "<table class='coverage'><thead><tr><th>Trace</th><th>Status</th>"
        "<th>Source requests</th><th>Replayed</th><th>Blocked</th>"
        "<th>Blocking flags (may overlap)</th><th>Frozen cache</th>"
        "<th>Tokens to fit every request</th><th>Publisher counts</th></tr></thead><tbody>"
    )
    lines = [header]
    for trace, item in results["traces"].items():
        blockers = (
            ", ".join(f"{k}: {v:,}" for k, v in item["blocker_counts"].items())
            or "none"
        )
        lines.append(
            f"<tr><th scope='row'>{html.escape(LABELS.get(trace, trace))}</th>"
            f"<td class='status {item['status']}'>{item['status']}</td>"
            f"<td>{item['observed']['requests']:,}</td>"
            f"<td>{item['replayable_requests'] if item['status'] == 'ready' else 0:,}</td>"
            f"<td>{item['blocked_requests']:,}</td><td class='text'>{html.escape(blockers)}</td>"
            f"<td>{item['cache_tokens']:,}</td><td>{item['cache_tokens_to_fit_every_request']:,}</td>"
            f"<td>{'match' if item['publisher_counts_match'] else 'differ'}</td></tr>"
        )
    lines.append("</tbody></table>")
    return "".join(lines)


def trace_section(results: dict, section: dict) -> str:
    item, labels = (
        section["item"],
        [p["label"] for p in results["policies"].values() if p["kind"] == "policy"],
    )
    parts = [
        f"<section id='{section['trace']}'><h2>{html.escape(section['label'])}</h2>"
        f"<p class='context'>{item['replayable_requests']:,} requests, one recording replayed from a cold cache with original timestamps · "
        f"cache {item['cache_tokens']:,} tokens ({item['capacity_blocks']} blocks) · workload SHA-256 {item['recording']['sha256'][:12]}…</p>"
        "<div class='scroll'><table><thead><tr><th scope='col'>Metric</th><th scope='col'>Unit</th>"
        + "".join(f"<th scope='col'>{html.escape(label)}</th>" for label in labels)
        + "</tr></thead><tbody>"
    ]
    group = None
    for row in section["rows"]:
        if row["group"] != group:
            group = row["group"]
            parts.append(
                f"<tr class='group'><th colspan='{len(labels) + 2}'>{html.escape(group)}</th></tr>"
            )
        parts.append(
            f"<tr><th scope='row'>{html.escape(row['metric'])}</th><td class='unit'>{html.escape(row['unit'])}</td>"
        )
        for index, value in enumerate(row["display"]):
            delta = (
                ""
                if index == 0
                else f"<span class='delta'>{change_text(row['changes'][index], row['change_kind'])}</span>"
            )
            parts.append(
                f"<td class='{'lead' if index in row['leaders'] else ''}'>{value}{delta}</td>"
            )
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    windows = item["arrival_windows"]
    names = [n for n, p in results["policies"].items() if p["kind"] == "policy"]
    for key, title, decimals in (
        ("mean_ttft_ms", "Mean TTFT (ms) by arrival window", 0),
        ("token_hit_pct", "Input-token hit rate (%) by arrival window", 2),
    ):
        parts.append(
            f"<h3>{title}</h3><div class='scroll'><table class='windows'><thead><tr><th>Arrivals from (s)</th><th>Requests</th>"
            + "".join(f"<th>{html.escape(label)}</th>" for label in labels)
            + "</tr></thead><tbody>"
        )
        for index, window in enumerate(windows[names[0]]):
            parts.append(
                f"<tr><th scope='row'>{window['arrival_start_s']:,.0f}</th><td>{window['requests']:,}</td>"
                + "".join(
                    f"<td>{fmt(windows[name][index][key], decimals)}</td>"
                    for name in names
                )
                + "</tr>"
            )
        parts.append("</tbody></table></div>")
    cadence = item["cadence_check"]
    parts.append(
        "<h3>Checks</h3><ul class='checks'>"
        + "".join(
            f"<li>{html.escape(name)}: {'identical' if all(files.values()) else 'DIFFERS'} "
            f"({', '.join(files)})</li>"
            for name, files in item["parity"].items()
        )
        + f"<li>Sampling error on the first {cadence['bounded_prefix_requests']:,} requests: "
        f"{cadence['frozen_interval_ms']:g} ms versus {cadence['finer_interval_ms']:g} ms cadence, largest absolute "
        f"difference {cadence['max_absolute_difference']:.4f} (percentage points or time fraction) across occupancy summaries.</li></ul></section>"
    )
    return "".join(parts)


def policy_list(results: dict, evidence: Path) -> str:
    parts = [
        "<section id='policies'><h2>Policies and checks</h2><table class='policies'><tbody>"
    ]
    for name, policy in results["policies"].items():
        source_path = evidence / "policies" / f"{name}.py"
        source = (
            f"<details><summary>source</summary><pre><code>{html.escape(source_path.read_text())}</code></pre></details>"
            if source_path.exists()
            else "native engine implementation"
        )
        parts.append(
            f"<tr><th scope='row'>{html.escape(policy['label'])}</th><td class='text'>{html.escape(policy['note'])}</td><td class='text'>{source}</td></tr>"
        )
    parts.append("</tbody></table></section>")
    return "".join(parts)


def archive(evidence: Path) -> bytes:
    """Compact evidence plus generator sources, with fixed timestamps."""
    base = Path("research/kv-eviction")
    location = (
        evidence.relative_to(ROOT)
        if evidence.is_relative_to(ROOT)
        else Path("evidence") / evidence.name
    )
    names = [*json.loads((evidence / "COMPLETE.json").read_text()), "COMPLETE.json"]
    paths = [(evidence / name, base / location / name) for name in sorted(names)]
    paths += [
        (ROOT / name, base / name) for name in GENERATOR_FILES if (ROOT / name).exists()
    ]
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as bundle:
        for source, name in paths:
            info = zipfile.ZipInfo(str(name), (2026, 10, 30, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, source.read_bytes())
    return content.getvalue()


STYLE = """
:root{--fg:#1f2328;--muted:#656d76;--line:#d8dee4;--head:#f6f8fa;--lead:#dafbe1;--lead-fg:#116329;--bg:#fff;--warn:#9a6700;--bad:#cf222e}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--fg:#e6edf3;--muted:#8d96a0;--line:#30363d;--head:#161b22;--lead:#12361f;--lead-fg:#7ee2a8;--bg:#0d1117;--warn:#d29922;--bad:#ff7b72}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
main{max-width:1500px;margin:0 auto;padding:28px 16px 40px}h1{font-size:24px;margin:0 0 6px}h2{font-size:19px;margin:30px 0 4px}h3{font-size:14px;margin:18px 0 6px}
p,li{max-width:110ch}.context,.muted{color:var(--muted);font-size:12.5px}.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:12.5px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right;vertical-align:top}thead th{background:var(--head);position:sticky;top:0}
tbody th,td.text,td.unit{text-align:left;font-weight:400}td.unit{color:var(--muted);white-space:nowrap}tr.group th{text-align:left;font-weight:650;background:var(--head)}
td.lead{background:var(--lead);color:var(--lead-fg);font-weight:650}.delta{display:block;color:var(--muted);font-size:11px;font-weight:400}
td.status.blocked{color:var(--bad);font-weight:650}td.status.ready{color:var(--lead-fg);font-weight:650}
.policies td,.policies th{text-align:left}pre{white-space:pre-wrap;font-size:12px;margin:6px 0}.checks{font-size:12.5px}
.callout{border-left:3px solid var(--warn);padding:6px 12px;background:var(--head);margin:14px 0}
"""


def page(results: dict, sections: list[dict], evidence: Path, bundle: bytes) -> str:
    settings = results["settings"]
    rank = settings["timing_profile"]["rank"]
    blocked = [
        LABELS.get(t, t) for t, i in results["traces"].items() if i["status"] != "ready"
    ]
    payload = base64.b64encode(bundle).decode("ascii")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Full-trace KV baselines</title><style>{STYLE}</style></head><body><main>"
        "<h1>Full-trace KV eviction baselines</h1>"
        "<p>How much do common eviction heuristics change KV-cache effectiveness, and through it simulated TTFT and ITL, on complete public workloads replayed with their original timestamps?</p>"
        "<p class='callout'>Simulation, not GPU measurement: one vLLM-style worker with the pinned engine's uncalibrated polynomial timing "
        f"(max {rank['max_num_seqs']} sequences, {rank['max_num_batched_tokens']:,} batched tokens per step, {settings['block_tokens']}-token blocks). "
        f"SLO: TTFT ≤ {settings['slo']['ttft_ms']:,} ms and per-request p95 ITL ≤ {settings['slo']['per_request_p95_itl_ms']} ms. "
        f"No arrival scaling, load sweep, tuning or agent calls. Run <code>{html.escape(results['run'])}</code>; {results['audited_jobs']} audited replays."
        + (
            f" Blocked before replay: {html.escape(', '.join(blocked))} (see coverage)."
            if blocked
            else ""
        )
        + "</p><h2>Coverage</h2>"
        + coverage_table(results)
        + "<p class='muted'>A trace is replayed only if every source request can be represented: no request may exceed the frozen cache, and zero or missing output counts and unsupported nested/overlapping requests block the whole trace instead of being filtered out.</p>"
        + "".join(trace_section(results, s) for s in sections)
        + policy_list(results, evidence)
        + "<section><h2>Definitions</h2><ul>"
        "<li>Columns compare heuristics on identical inputs; changes are versus LRU on the same trace: relative % for counts and times, percentage points (pp) for rates and shares. Green marks the best displayed value of an outcome metric; ties and all-equal rows are unmarked. Occupancy rows are diagnostics, not rankings.</li>"
        "<li>TTFT includes queueing. ITL pools every consecutive output-token gap; percentiles are nearest rank. One-token outputs have no ITL criterion; zero-output requests are not SLO-eligible. Goodput divides SLO passes by the span from first arrival to last terminal event, including drain.</li>"
        "<li>Hit rate counts input tokens reused at first admission; recomputed tokens include repeated prefill after preemption. Evictions count 512-token blocks.</li>"
        f"<li>KV occupancy comes from native simulated-time telemetry every {settings['telemetry']['sample_interval_ms']:g} ms, duration-weighted with sample-and-hold. Physical = active + inactive (reclaimable) blocks; active = referenced or reserved. Peaks and threshold times are sampled estimates. Request-active intervals start with at least one request in the system. A physically full cache of reclaimable blocks is not active-memory pressure.</li>"
        "<li>Random seeds 0, 1 and 2 are separate runs; none is selected or averaged. Deterministic repeats show reproducibility, not statistical independence.</li>"
        "</ul></section>"
        "<section><h2>Reproduce</h2><pre><code>python3 research/kv-eviction/prepare_native.py\n"
        "python3 research/kv-eviction/baseline_comparison.py fetch --config research/kv-eviction/configs/full-trace-baselines.json\n"
        "python3 research/kv-eviction/baseline_comparison.py run --config research/kv-eviction/configs/full-trace-baselines.json --output research/kv-eviction/experiments/&lt;new-date&gt;\n"
        "python3 research/kv-eviction/baseline_comparison.py audit --run research/kv-eviction/experiments/&lt;new-date&gt; --evidence research/kv-eviction/evidence/&lt;new-name&gt;\n"
        "python3 research/kv-eviction/export_report.py --baseline-evidence research/kv-eviction/evidence/full-trace-baselines --output research/kv-eviction/reports/full-trace-baselines.html</code></pre>"
        f"<p><a download='full-trace-baselines-evidence.zip' href='data:application/zip;base64,{payload}'>Download the compact evidence and report generator</a> (verified against COMPLETE.json when this page was generated).</p></section>"
        "</main></body></html>\n"
    )


def build(evidence: Path, output: Path) -> dict:
    evidence, output = evidence.resolve(), output.resolve()
    results = load(evidence)
    sections = tables(results)
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = archive(evidence)
    output.write_text(page(results, sections, evidence, bundle))
    rows = csv_rows(results, sections)
    with output.with_suffix(".csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return {
        "html": str(output),
        "csv": str(output.with_suffix(".csv")),
        "traces_reported": [s["trace"] for s in sections],
        "evidence_files_verified": len(
            json.loads((evidence / "COMPLETE.json").read_text())
        ),
    }
