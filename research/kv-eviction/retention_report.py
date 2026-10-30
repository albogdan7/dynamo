"""Offline HTML and CSV report for the agentic retention experiment.

Reads only checksummed compact evidence, states the frozen decision first,
then shows every split, load level and policy. Never simulates.
"""

import base64
import csv
import html
import io
import json
import zipfile
from pathlib import Path

from baseline_report import (
    STYLE,
    change,
    change_text,
    field,
    fmt,
    leaders,
    occupancy,
    require,
    sha,
)

ROOT = Path(__file__).resolve().parent
GENERATOR_FILES = [
    "retention_report.py",
    "baseline_report.py",
    "export_report.py",
    "summarize.py",
    "retention_experiment.py",
    "kv_experiment/retention.py",
    "kv_experiment/retention_evidence.py",
    "kv_experiment/agentic.py",
    "kv_experiment/summary_metrics.py",
    "evidence/audit_run.py",
    "configs/agentic-retention.json",
    "configs/agentic-reload-calibration.json",
    "AGENTIC_RETENTION_SPEC.md",
    "AGENTIC_RETENTION_RESULTS.md",
]
H = 3_600_000
# fmt: off
ROWS = [
    ("Latency", "Mean request latency (arrival to last token) — primary", "s", field("mean_request_latency_ms", 1e-3), 3, "lower", "relative"),
    ("Latency", "p95 request latency", "s", field("p95_request_latency_ms", 1e-3), 3, "lower", "relative"),
    ("Latency", "Mean TTFT, including queueing", "ms", field("mean_ttft_ms"), 1, "lower", "relative"),
    ("Latency", "p95 TTFT", "ms", field("p95_ttft_ms"), 1, "lower", "relative"),
    ("Latency", "Mean ITL over individual gaps", "ms/gap", field("mean_itl_ms"), 3, "lower", "relative"),
    ("Latency", "p95 ITL over individual gaps", "ms/gap", field("p95_itl_ms"), 3, "lower", "relative"),
    ("SLO", "SLO attainment (TTFT ≤ 1 s and p95 ITL ≤ 50 ms)", "% of eligible", field("slo_attainment_pct"), 3, "higher", "points"),
    ("SLO", "SLO passes", "requests", field("slo_passes"), 0, "higher", "relative"),
    ("Cache effectiveness", "Input-token hit rate (first admission)", "%", field("token_hit_pct"), 3, "higher", "points"),
    ("Cache effectiveness", "Recomputed input tokens", "tokens", field("recomputed_input_tokens"), 0, "lower", "relative"),
    ("Cache effectiveness", "Evicted blocks", "512-token blocks", field("evictions"), 0, None, "relative"),
    ("Cache effectiveness", "Forced evictions of protected blocks", "blocks", field("protected_evictions"), 0, None, "relative"),
    ("KV occupancy, whole run", "Physical (active + inactive), mean", "% of capacity", occupancy("physical_blocks", "mean_pct"), 2, None, "points"),
    ("KV occupancy, whole run", "Active, mean", "% of capacity", occupancy("active_blocks", "mean_pct"), 2, None, "points"),
    ("KV occupancy, whole run", "Time with active ≥ 95% of capacity", "% of time", occupancy("active_blocks", "time_fraction_at_or_above_95pct", scale=100), 2, None, "points"),
    ("KV occupancy, whole run", "Inactive (evictable), mean", "% of capacity", occupancy("inactive_blocks", "mean_pct"), 2, None, "points"),
    ("Pressure and completion", "Waiting queue, time-weighted mean", "requests", occupancy("waiting_requests", "mean"), 2, None, "relative"),
    ("Pressure and completion", "Waiting queue, sampled peak", "requests", occupancy("waiting_requests", "peak_sampled"), 0, None, "relative"),
    ("Pressure and completion", "Preemptions", "events", lambda m: m["occupancy"]["preemptions"], 0, None, "relative"),
    ("Pressure and completion", "Completed requests", "requests", field("completed"), 0, None, "relative"),
    ("Pressure and completion", "Mean play span (dominated by recorded idle gaps)", "h", field("mean_play_span_ms", 1 / H), 3, None, "relative"),
    ("Pressure and completion", "Observation span", "h", field("observation_duration_ms", 1 / H), 1, None, "relative"),
]
# fmt: on


def load(evidence: Path) -> dict:
    complete = json.loads((evidence / "COMPLETE.json").read_text())
    present = {
        str(p.relative_to(evidence))
        for p in evidence.rglob("*")
        if p.is_file() and p.name != "COMPLETE.json"
    }
    require(present == set(complete), "Evidence files differ from COMPLETE.json")
    for name, digest in complete.items():
        require(sha(evidence / name) == digest, f"Evidence file changed: {name}")
    results = json.loads((evidence / "results.json").read_text())
    require(
        all(all(files.values()) for files in results["parity"].values()),
        "Parity failed",
    )
    return results


def cells(results: dict):
    names = list(results["policies"])
    for split in ("evaluation", "tuning"):
        for lanes, policies in results["results"][split].items():
            metrics = [policies[name] for name in names]
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
                        "kind": kind,
                        "leaders": leaders(values, better, decimals),
                    }
                )
            yield split, lanes, rows


def decision_block(results: dict) -> str:
    decision = results["decision"]
    names = [n for n in results["policies"] if n != "lru"]
    labels = [results["policies"][n]["label"] for n in names]
    rule = decision["rule"]
    parts = [
        f"<section id='decision'><h2>Decision (evaluation split, frozen rule)</h2>"
        f"<p class='callout'><strong>{html.escape(decision['verdict'])}.</strong> "
        f"Rule: a non-oracle policy cuts mean request latency by ≥ {rule['real_policy_min_reduction_pct']}% versus LRU, "
        f"or the oracle by ≥ {rule['oracle_min_reduction_pct']}%, at ≥ {rule['min_load_levels']} of {len(decision['levels'])} load levels. "
        f"Met by a real policy at {decision['real_policy_levels_meeting_threshold']} level(s); by the oracle at {decision['oracle_levels_meeting_threshold']}.</p>"
        "<div class='scroll'><table><thead><tr><th>Concurrent plays</th><th>LRU mean latency (s)</th>"
        + "".join(f"<th>{html.escape(label)}</th>" for label in labels)
        + "</tr></thead><tbody>"
    ]
    for lanes, level in decision["levels"].items():
        parts.append(
            f"<tr><th scope='row'>{lanes}</th><td>{level['lru_ms'] / 1000:,.3f}</td>"
            + "".join(
                f"<td class='{'lead' if n == level['best_real_policy'] else ''}'>{level['reduction_pct'][n]:+.2f}%</td>"
                for n in names
            )
            + "</tr>"
        )
    parts.append(
        "</tbody></table></div><p class='muted'>Cells show the reduction in mean request latency versus LRU (positive is faster). "
        "Green marks the best non-oracle policy at each level. Oracle and unbounded cache are bounds, not deployable policies.</p></section>"
    )
    return "".join(parts)


def table(results: dict, split: str, lanes: str, rows: list[dict]) -> str:
    labels = [p["label"] for p in results["policies"].values()]
    requests = results["coverage_requests"][f"{split}/{lanes}"]
    parts = [
        f"<section id='{split}-{lanes}'><h2>{split.title()} split · {lanes.replace('lanes-', '')} concurrent plays</h2>"
        f"<p class='context'>{requests:,} requests, every one replayed; cold start; changes versus LRU in the same cell.</p>"
        "<div class='scroll'><table><thead><tr><th>Metric</th><th>Unit</th>"
        + "".join(f"<th>{html.escape(label)}</th>" for label in labels)
        + "</tr></thead><tbody>"
    ]
    group = None
    for row in rows:
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
                else f"<span class='delta'>{change_text(row['changes'][index], row['kind'])}</span>"
            )
            parts.append(
                f"<td class='{'lead' if index in row['leaders'] else ''}'>{value}{delta}</td>"
            )
        parts.append("</tr>")
    parts.append("</tbody></table></div></section>")
    return "".join(parts)


def checks(results: dict, evidence: Path) -> str:
    sampling = results["sampling_error"]
    inventory = results["inventory"]
    items = [
        f"{name}: {'identical' if all(files.values()) else 'DIFFERS'} ({', '.join(files)})"
        for name, files in results["parity"].items()
    ]
    items.append(
        f"Coverage: all {inventory['requests']:,} AgentX requests from {inventory['plays']} plays, split "
        f"{inventory['splits']['tuning']['requests']:,} tuning / {inventory['splits']['evaluation']['requests']:,} evaluation; "
        f"{inventory['zero_output_requests']} zero-output requests replayed prompt-only."
    )
    items.append(
        f"Occupancy sampling error ({sampling['frozen_interval_ms']:g} ms vs {sampling['finer_interval_ms']:g} ms on a bounded fixture): "
        f"at most {sampling['max_absolute_difference_pp']:.4f} percentage points."
    )
    items.append(
        f"{results['audited_jobs']} replays independently re-audited; previous attempts: {len(results['previous_attempts'])}."
    )
    policies = "".join(
        f"<tr><th scope='row'>{html.escape(p['label'])}</th><td class='text'>{html.escape(p['note'])}</td></tr>"
        for p in results["policies"].values()
    )
    sources = "".join(
        f"<details><summary>{html.escape(path.stem)} (callback parity source)</summary><pre><code>{html.escape(path.read_text())}</code></pre></details>"
        for path in sorted((evidence / "policies").glob("*.py"))
    )
    return (
        "<section id='checks'><h2>Checks</h2><ul class='checks'>"
        + "".join(f"<li>{html.escape(i)}</li>" for i in items)
        + "</ul></section><section id='policies'><h2>Policies</h2><table class='policies'><tbody>"
        + policies
        + "</tbody></table>"
        + sources
        + "</section>"
    )


def archive(evidence: Path) -> bytes:
    base = Path("research/kv-eviction")
    location = (
        evidence.relative_to(ROOT)
        if evidence.is_relative_to(ROOT)
        else Path("evidence") / evidence.name
    )
    names = [*json.loads((evidence / "COMPLETE.json").read_text()), "COMPLETE.json"]
    paths = [(evidence / n, base / location / n) for n in sorted(names)]
    paths += [(ROOT / n, base / n) for n in GENERATOR_FILES if (ROOT / n).exists()]
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as bundle:
        for source, name in paths:
            info = zipfile.ZipInfo(str(name), (2026, 10, 30, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, source.read_bytes())
    return content.getvalue()


def build(evidence: Path, output: Path) -> dict:
    evidence, output = evidence.resolve(), output.resolve()
    results = load(evidence)
    settings = results["settings"]
    tables = list(cells(results))
    bundle = base64.b64encode(archive(evidence)).decode("ascii")
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Agentic KV retention</title><style>{STYLE}</style></head><body><main>"
        "<h1>Agentic KV retention</h1>"
        "<p>When agent sessions pause between requests, does a retention policy that predicts each session's return reduce simulated request latency versus LRU, and how much headroom does an oracle show?</p>"
        "<p class='callout'>Simulation, not GPU measurement: complete AgentX (393 plays) on one vLLM-style worker with uncalibrated polynomial timing; "
        f"cache {settings['cache_blocks'] * settings['block_tokens']:,} tokens ({settings['served_model']['name']} on one {settings['hardware']['gpu']}); "
        f"{settings['scheduler']['max_num_seqs']} sequences and {settings['scheduler']['max_num_batched_tokens']:,} tokens per step; "
        f"closed loop with {', '.join(map(str, settings['lanes']))} concurrent plays. Retention is a soft eviction priority and never blocks admission. "
        f"Run <code>{html.escape(results['run'])}</code>.</p>"
        + decision_block(results)
        + "".join(table(results, split, lanes, rows) for split, lanes, rows in tables)
        + checks(results, evidence)
        + "<section><h2>Reproduce</h2><pre><code>python3 research/kv-eviction/prepare_native.py\n"
        "python3 research/kv-eviction/retention_experiment.py prepare --config research/kv-eviction/configs/agentic-retention.json\n"
        "python3 research/kv-eviction/retention_experiment.py run --config research/kv-eviction/configs/agentic-retention.json --output research/kv-eviction/experiments/&lt;new-date&gt;\n"
        "python3 research/kv-eviction/retention_experiment.py audit --run research/kv-eviction/experiments/&lt;new-date&gt; --evidence research/kv-eviction/evidence/&lt;new-name&gt;\n"
        "python3 research/kv-eviction/export_report.py --retention-evidence research/kv-eviction/evidence/agentic-retention --output research/kv-eviction/reports/agentic-retention.html</code></pre>"
        f"<p><a download='agentic-retention-evidence.zip' href='data:application/zip;base64,{bundle}'>Download the compact evidence and report generator</a></p></section>"
        "</main></body></html>\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page)
    names = list(results["policies"])
    records = []
    for split, lanes, rows in tables:
        for row in rows:
            for index, name in enumerate(names):
                records.append(
                    {
                        "split": split,
                        "concurrent_plays": lanes.replace("lanes-", ""),
                        "policy": name,
                        "group": row["group"],
                        "metric": row["metric"],
                        "unit": row["unit"],
                        "value": row["values"][index],
                        "change_vs_lru": row["changes"][index],
                        "change_kind": "percentage points"
                        if row["kind"] == "points"
                        else "relative %",
                    }
                )
    with output.with_suffix(".csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(records[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(records)
    return {
        "html": str(output),
        "csv": str(output.with_suffix(".csv")),
        "verdict": results["decision"]["verdict"],
    }
