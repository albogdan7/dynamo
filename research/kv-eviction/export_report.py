"""Build an auditable report from saved evidence, without calling an agent.

Standard library: HTML, Markdown, CSV, JSON and a self-contained evidence ZIP.
The HTML embeds its evidence and policy dialogs and needs no external assets.
"""

import argparse
import ast
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


def comparison_root(root):
    pointer = root / "evidence/current-comparison.json"
    return (
        evidence_path(root, read(pointer)["directory"])
        if pointer.exists()
        else root / "evidence/comparison"
    )


def validate(root, raw_root=None):
    """Verify sources, policies, selection and receipts before reporting scores."""
    runs = {}
    files = {}
    audit_count = 0
    raw_count = 0
    pointer = root / "evidence/current-comparison.json"
    if pointer.exists():
        files[str(pointer.relative_to(root))] = sha(pointer)
    for filename in sorted((comparison_root(root) / "runs").glob("*.json")):
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
            if not status["valid"]:
                require(
                    "tuning" not in status, "Invalid submission has a successful score"
                )
                if submission.get("policy_path"):
                    source = evidence_path(root, submission["policy_path"])
                    require(
                        sha(source) == submission["source_sha256"],
                        "Failed submission source changed",
                    )
                    files[str(source.relative_to(root))] = sha(source)
                statuses.append(status)
                continue
            policy = evidence_path(root, submission["policy_path"])
            require(sha(policy) == status["policy_sha256"], f"Policy changed: {policy}")
            files[str(policy.relative_to(root))] = sha(policy)
            statuses.append(status)
        require(
            len(statuses) == manifest["submission_budget"], "Submission count mismatch"
        )
        ordered = sorted(
            (item for item in statuses if item["valid"]),
            key=lambda item: item["ordinal"],
        )
        require(ordered, "No valid policies")
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
        for metrics in [record["evaluation"], *(s["tuning"] for s in ordered)]:
            expected = metrics["slo_passes"] * 1000 / metrics["observation_duration_ms"]
            require(
                math.isclose(metrics["goodput_rps"], expected, rel_tol=1e-12),
                "Goodput arithmetic mismatch",
            )
        for name in ("replay-check.json", "independent-audit.json"):
            files[str((bundle / name).relative_to(root))] = sha(bundle / name)
        for baseline_name, specification in record.get("baseline_sources", {}).items():
            source = evidence_path(root, specification["path"])
            require(sha(source) == specification["sha256"], "Baseline source changed")
            require(
                source.read_text()
                == manifest["heuristic_baselines"][baseline_name]["source"],
                "Baseline source differs from frozen definition",
            )
            files[str(source.relative_to(root))] = sha(source)
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
    return runs, {
        "input_sha256": files,
        "saved_audit_receipts": audit_count,
        "raw_replays_reaudited": raw_count,
        "verified_policy_sources": sum(
            bool(s.get("policy_path")) for r in runs.values() for s in r["submissions"]
        ),
        "valid_submissions": sum(
            s["status"]["valid"] for r in runs.values() for s in r["submissions"]
        ),
        "invalid_submissions": sum(
            not s["status"]["valid"] for r in runs.values() for s in r["submissions"]
        ),
        "verified_source_snapshots": len(runs),
        "selected_replay_receipts": len(runs),
    }


def fmt(value, decimals=3):
    return "unavailable" if value is None else f"{value:,.{decimals}f}"


def delta(value, baseline):
    return 100 * (value / baseline - 1) if baseline else None


HIGHER_IS_BETTER = {"goodput_rps", "slo_attainment_pct", "token_hit_pct", "slo_passes"}
LOWER_IS_BETTER = {
    "mean_ttft_ms",
    "p95_ttft_ms",
    "mean_itl_ms",
    "p95_itl_ms",
    "recomputed_input_tokens",
    "evictions",
}


def best_columns(values, higher, digits):
    """Rank displayed precision; do not imply a winner when every value ties."""
    rounded = [round(value, digits) if value is not None else None for value in values]
    available = [value for value in rounded if value is not None]
    if len(available) < 2 or len(set(available)) == 1:
        return []
    best = (max if higher else min)(available)
    return [index for index, value in enumerate(rounded) if value == best]


def usage(record):
    results = {
        key: 0 for key in ("input", "cache_read", "cache_write", "output", "seconds")
    }
    costs = []
    for submission in record["submissions"]:
        metadata = submission["agent_metadata"]
        if metadata is None:
            return {**{key: None for key in results}, "usd": None}
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


def baseline_source(root, record):
    """Read the equivalent LRU callback from the exact trial controller snapshot."""
    with zipfile.ZipFile(
        evidence_path(root, record["source_snapshot_path"])
    ) as archive:
        tree = ast.parse(archive.read("research/kv-eviction/experiment.py").decode())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "LRU"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("Recorded controller has no LRU callback")


def sections(root, runs):
    traces = []
    for trace in TRACES:
        left, right = runs[trace, "claude"], runs[trace, "codex"]
        policies = [
            {
                "id": trace + "-lru",
                "label": "LRU",
                "source": baseline_source(root, left),
                "note": "Python callback equivalent to the native LRU baseline. Baseline scores use the native implementation.",
            }
        ]
        for name, specification in (
            left["manifest"].get("heuristic_baselines", {}).items()
        ):
            source = left["baseline_sources"][name]
            policies.append(
                {
                    "id": trace + "-" + name,
                    "label": specification["label"],
                    "source": evidence_path(root, source["path"]).read_text(),
                    "note": specification["note"],
                    "baseline": name,
                }
            )
        for agent, record in (("claude", left), ("codex", right)):
            selected = next(
                item
                for item in record["submissions"]
                if item["id"] == record["selection"]["submission_id"]
            )
            policies.append(
                {
                    "id": trace + "-" + agent,
                    "label": "Selected " + agent.title() + " policy",
                    "source": evidence_path(root, selected["policy_path"]).read_text(),
                    "note": record["model"]
                    + (" (requested model)" if agent == "codex" else "")
                    + " · submission "
                    + selected["id"],
                }
            )
        comparisons = []
        for split, title in (
            ("tuning", "Tuning performance"),
            ("evaluation", "Held-out performance"),
        ):
            metrics = [left["baselines"]["native-lru"][split]]
            metrics.extend(
                left["baselines"][name][split]
                for name in left["manifest"].get("heuristic_baselines", {})
            )
            metrics.extend(
                record["selection"]["tuning"]
                if split == "tuning"
                else record["evaluation"]
                for record in (left, right)
            )
            rows = []
            for field, label, unit, digits in FIELDS:
                values = [item[field] for item in metrics]
                rows.append(
                    {
                        "metric": field,
                        "label": f"{label} ({unit})",
                        "values": [fmt(value, digits) for value in values],
                        "changes": [delta(value, values[0]) for value in values],
                        "best": best_columns(values, field in HIGHER_IS_BETTER, digits)
                        if field in HIGHER_IS_BETTER | LOWER_IS_BETTER
                        else [],
                    }
                )
            rows.append(
                {
                    "metric": "slo_passes",
                    "label": "SLO passes / eligible",
                    "values": [
                        f"{item['slo_passes']} / {item['slo_eligible_requests']}"
                        for item in metrics
                    ],
                    "changes": [
                        delta(item["slo_passes"], metrics[0]["slo_passes"])
                        for item in metrics
                    ],
                    "best": best_columns(
                        [item["slo_passes"] for item in metrics], True, 0
                    ),
                }
            )
            rows.append(
                {
                    "metric": "observation_duration_ms",
                    "label": "Observation span (seconds)",
                    "values": [
                        fmt(item["observation_duration_ms"] / 1000, 3)
                        for item in metrics
                    ],
                    "changes": [
                        delta(
                            item["observation_duration_ms"],
                            metrics[0]["observation_duration_ms"],
                        )
                        for item in metrics
                    ],
                    "best": [],
                }
            )
            comparisons.append(
                {
                    "split": split,
                    "title": title,
                    "requests": metrics[0]["requests"],
                    "rows": rows,
                }
            )
        rank = left["manifest"]["timing"]["rank"]
        traces.append(
            {
                "trace": trace,
                "label": LABELS[trace],
                "cache_tokens": rank["num_gpu_blocks"] * rank["block_size"],
                "policies": policies,
                "tables": comparisons,
            }
        )
    return traces


def change_text(value):
    if value is None:
        return "undefined vs zero LRU"
    if abs(value) < 1e-12:
        return "0%"
    return f"{value:+.{6 if abs(value) < 0.001 else 3}f}%"


def markdown(traces):
    lines = [
        "<!-- Generated by export_report.py. Do not edit. -->",
        "# KV eviction results",
        "",
        "Uncalibrated simulation. Changes are relative to LRU on the same split.",
        "",
    ]
    for trace in traces:
        lines.extend(["## " + trace["label"], ""])
        for comparison in trace["tables"]:
            lines.extend(
                [
                    "### " + comparison["title"],
                    "",
                    "| Metric and unit | "
                    + " | ".join(policy["label"] for policy in trace["policies"])
                    + " |",
                    "| "
                    + " | ".join("---" for _ in range(1 + len(trace["policies"])))
                    + " |",
                ]
            )
            for row in comparison["rows"]:
                cells = [
                    row["values"][0],
                    *(
                        f"{value} ({change_text(change)} vs LRU)"
                        for value, change in zip(row["values"][1:], row["changes"][1:])
                    ),
                ]
                lines.append("| " + " | ".join([row["label"], *cells]) + " |")
            lines.append("")
    return "\n".join(lines)


def reproduction_archive(root):
    paths = {
        root / name
        for name in (
            "export_report.py",
            "summarize.py",
            "run_comparison.py",
            "collect_comparison.py",
            "REPORT_EXPORT.md",
            "TRACE_SOURCES.md",
            "HEURISTIC_BASELINES.md",
            "README.md",
            "evidence/audit_run.py",
        )
    }
    paths.update(comparison_root(root).rglob("*"))
    if (root / "evidence/current-comparison.json").exists():
        paths.add(root / "evidence/current-comparison.json")
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


def webpage(traces, reproduction):
    parts = []
    for trace in traces:
        parts.append(
            f'<section id="{trace["trace"]}"><h2>{html.escape(trace["label"])}</h2><p class="context">Cache {trace["cache_tokens"]:,} tokens</p>'
        )
        for comparison in trace["tables"]:
            parts.append(
                f'<h3>{comparison["title"]} <span>{comparison["requests"]} requests</span></h3><div class="table-scroll"><table><thead><tr><th scope="col">Metric and unit</th>'
                + "".join(
                    f'<th scope="col">{html.escape(policy["label"])}</th>'
                    for policy in trace["policies"]
                )
                + "</tr></thead><tbody>"
            )
            for row in comparison["rows"]:
                parts.append(
                    f'<tr data-metric="{row["metric"]}"><th scope="row">{html.escape(row["label"])}</th>'
                )
                for index, policy in enumerate(trace["policies"]):
                    accessible = html.escape(
                        f"View {policy['label']} for {trace['label']}", quote=True
                    )
                    change = (
                        ""
                        if index == 0
                        else f'<span class="change">{change_text(row["changes"][index])}</span>'
                    )
                    parts.append(
                        f'<td class="{"best" if index in row["best"] else ""}"><div class="value"><span>{row["values"][index]}</span><button type="button" class="view-policy" data-policy="{policy["id"]}" aria-label="{accessible}" title="{accessible}">&lt;/&gt;</button></div>{change}</td>'
                    )
                parts.append("</tr>")
            parts.append("</tbody></table></div>")
        parts.append("</section>")
        for policy in trace["policies"]:
            parts.append(
                f'<dialog id="{policy["id"]}" aria-labelledby="{policy["id"]}-title"><div class="modal-heading"><h2 id="{policy["id"]}-title">{html.escape(policy["label"])}</h2><form method="dialog"><button class="close" autofocus>Close</button></form></div><p>{html.escape(trace["label"])}</p><p class="policy-note">{html.escape(policy["note"])}</p><pre><code>{html.escape(policy["source"])}</code></pre></dialog>'
            )
    payload = base64.b64encode(reproduction).decode("ascii")
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KV eviction results</title>
<style>
*{box-sizing:border-box}body{margin:32px auto;padding:0 22px;max-width:1900px;font:15px/1.5 system-ui,sans-serif;color:#202020;background:white}h1{font-size:26px;letter-spacing:-.5px;margin:0 0 8px}h2{font-size:21px;margin:0}h3{font-size:16px;font-weight:600;margin:20px 0 8px}h3 span{font-size:12px;font-weight:400;color:#666;margin-left:8px}.intro,.context,footer{color:#666;font-size:12px}.intro{margin:0 0 28px}.context{margin:3px 0 16px}section{padding:0 0 32px;margin:0 0 30px;border-bottom:1px solid #ddd}.table-scroll{overflow-x:auto}table{width:100%;min-width:1460px;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{padding:8px 8px;border-bottom:1px solid #e8e8e8;vertical-align:middle}thead th{font-size:11px;font-weight:600;background:#f4f4f4;text-align:right;color:#333}thead th:first-child{text-align:left;width:220px}tbody th{text-align:left;font-size:13px;font-weight:400}td{text-align:right;font-size:12px}td.best{background:#e8f3ec}td.best .value>span{font-weight:650;color:#20553a}tbody th{position:sticky;left:0;background:#fff;z-index:1}thead th{position:sticky;top:0;z-index:2}thead th:first-child{left:0;z-index:3}.value{display:flex;align-items:center;justify-content:flex-end;gap:4px;white-space:nowrap}.change{display:block;color:#777;font-size:11px;margin-right:23px;white-space:nowrap}.view-policy{font:11px ui-monospace,monospace;padding:3px 2px;color:#666;background:transparent;border:0;border-radius:3px;cursor:pointer}.view-policy:hover{background:#eee;color:#111}button:focus-visible,a:focus-visible{outline:2px solid #555;outline-offset:2px}dialog{border:1px solid #ddd;border-radius:6px;width:min(760px,calc(100% - 32px));max-height:85vh;padding:22px;box-shadow:0 12px 45px #0002}dialog::backdrop{background:#0005}.modal-heading{display:flex;justify-content:space-between;align-items:center;gap:12px}.modal-heading h2{font-size:19px}dialog p{font-size:13px;margin:8px 0}.policy-note{color:#666}dialog pre{margin:18px 0 0;overflow:auto;font:13px/1.6 ui-monospace,monospace;white-space:pre}form{margin:0}.close{font:inherit;font-size:13px;border:1px solid #ccc;border-radius:4px;padding:5px 10px;background:white;cursor:pointer}footer{margin:0 0 28px}footer a{color:inherit}footer p{margin:5px 0}@media(max-width:600px){body{padding:0 12px;margin:20px auto}h1{font-size:23px}th,td{padding:8px}dialog{padding:15px}dialog pre{font-size:12px}}@media print{body{font-size:10pt;margin:0;padding:0;max-width:none}.view-policy,dialog,footer{display:none}.table-scroll{overflow:visible}table{min-width:0;font-size:7pt}th,td{padding:5px 4px}tbody th{position:static}section{break-before:page;border:0;margin:0;padding:0}section:first-of-type{break-before:auto}tr{break-inside:avoid}.change{margin-right:0}}
</style></head><body><main><h1>KV eviction results</h1><p class="intro">Uncalibrated simulation · SLO: TTFT ≤ 1,000 ms and per-request p95 ITL ≤ 50 ms · Percentages show relative change from LRU. Click &lt;/&gt; beside a value to view its policy.</p>
"""
        + "\n".join(parts)
        + f'<footer><p>Green marks the best value per metric, including ties at displayed precision; fewer evictions are highlighted. All-equal rows are unmarked. Observation span is diagnostic and is not ranked.</p><p>Δ = 100 × (policy − LRU) / LRU. Percentage-valued metrics also use relative change. Random seeds 0, 1 and 2 are separate runs, not an average or a best-seed selection.</p><p><a download="kv-eviction-reproduce.zip" href="data:application/zip;base64,{payload}">Reproduction files</a></p></footer></main><noscript>Enable JavaScript to open policy source dialogs.</noscript>'
        + """
<script>
document.addEventListener('click', function(event) {
  const button = event.target.closest('button[data-policy]');
  if (button) {
    document.getElementById(button.dataset.policy).showModal();
    return;
  }
  if (event.target.tagName === 'DIALOG') {
    const bounds = event.target.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) event.target.close();
  }
});
</script></body></html>
"""
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
    summarize(comparison_root(root) / "runs", output)
    pages = sections(root, runs)
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
                        "valid": status["valid"],
                        "policy_sha256": status.get(
                            "policy_sha256", sub.get("source_sha256")
                        ),
                        "policy_path": sub["policy_path"],
                        **{
                            field: status.get("tuning", {}).get(field)
                            for field in METRICS
                        },
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
    parser.add_argument(
        "--baseline-evidence",
        type=Path,
        help="Report audited full-trace heuristic evidence instead of agent runs; "
        "--output is then the HTML path and a sibling CSV is written",
    )
    args = parser.parse_args()
    if args.baseline_evidence:
        from baseline_report import build as build_baselines

        print(
            json.dumps(build_baselines(args.baseline_evidence, args.output), indent=2)
        )
    else:
        build(ROOT, args.output, args.raw_root)
