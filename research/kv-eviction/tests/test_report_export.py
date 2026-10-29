"""Exercise the delivered HTML and its embedded offline reproduction path."""

import base64
import csv
import io
import json
import subprocess
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from export_report import (
    ROOT,
    build,
    delta,
    fmt,
    reproduction_archive,
    sections,
    validate,
)


class Downloads(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "a" and "download" in attributes:
            self.links.append(attributes["href"])


def test_embedded_bundle_rebuilds_identical_html_and_scores(tmp_path):
    original = tmp_path / "original"
    build(ROOT, original)
    parser = Downloads()
    parser.feed((original / "kv-eviction-results.html").read_text())
    assert len(parser.links) == 1
    prefix, encoded = parser.links[0].split(",", 1)
    assert prefix == "data:application/zip;base64"
    payload = base64.b64decode(encoded, validate=True)
    assert payload == (original / "reproduction.zip").read_bytes()
    extracted = tmp_path / "recipient"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(extracted)
    subprocess.run(
        [sys.executable, "research/kv-eviction/export_report.py"],
        cwd=extracted,
        check=True,
        capture_output=True,
        text=True,
    )
    rebuilt = extracted / "research/kv-eviction/reports"
    for name in (
        "kv-eviction-results.html",
        "results.csv",
        "submissions.csv",
        "agent-usage.csv",
        "report-manifest.json",
        "reproduction.zip",
    ):
        assert (original / name).read_bytes() == (rebuilt / name).read_bytes(), name
    with (rebuilt / "submissions.csv").open() as source:
        assert len(list(csv.DictReader(source))) == 40
    with (rebuilt / "results.csv").open() as source:
        assert len(list(csv.DictReader(source))) == 12


@pytest.fixture
def copied_evidence(tmp_path):
    with zipfile.ZipFile(io.BytesIO(reproduction_archive(ROOT))) as archive:
        archive.extractall(tmp_path)
    return tmp_path / "research/kv-eviction"


def test_report_refuses_changed_policy(copied_evidence, tmp_path):
    policy = next(
        (copied_evidence / "evidence/comparison/agentx-codex/policies").glob("*.py")
    )
    policy.write_text("class EvictionPolicy: pass\n")
    with pytest.raises(ValueError, match="Policy changed"):
        build(copied_evidence, tmp_path / "bad-report")


def test_report_refuses_mismatched_comparison(copied_evidence, tmp_path):
    path = copied_evidence / "evidence/comparison/runs/agentx-codex.json"
    record = json.loads(path.read_text())
    record["manifest"]["config_sha256"] = "0" * 64
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Mismatched config_sha256"):
        build(copied_evidence, tmp_path / "bad-report")


class Policies(HTMLParser):
    def __init__(self):
        super().__init__()
        self.dialogs = {}
        self.buttons = []
        self.current = None
        self.in_code = False

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "dialog":
            self.current = attributes["id"]
            self.dialogs[self.current] = ""
        elif tag == "code" and self.current:
            self.in_code = True
        elif tag == "button" and "data-policy" in attributes:
            self.buttons.append(attributes["data-policy"])

    def handle_endtag(self, tag):
        if tag == "code":
            self.in_code = False
        elif tag == "dialog":
            self.current = None

    def handle_data(self, data):
        if self.in_code:
            self.dialogs[self.current] += data


def test_every_value_opens_its_exact_policy_source(tmp_path):
    build(ROOT, tmp_path)
    parser = Policies()
    parser.feed((tmp_path / "kv-eviction-results.html").read_text())
    assert len(parser.dialogs) == 12
    assert len(parser.buttons) == 4 * 2 * 11 * 3
    assert set(parser.buttons) == set(parser.dialogs)
    for path in (ROOT / "evidence/comparison/runs").glob("*.json"):
        record = json.loads(path.read_text())
        selected = next(
            s
            for s in record["submissions"]
            if s["id"] == record["selection"]["submission_id"]
        )
        assert (
            parser.dialogs[f"{record['trace']}-{record['agent']}"]
            == (ROOT / selected["policy_path"]).read_text()
        )
    for identity, source in parser.dialogs.items():
        assert parser.buttons.count(identity) == 22
        if identity.endswith("-lru"):
            assert 'min(blocks, key=lambda block: block["lru_rank"])' in source


def test_tuning_values_and_rate_changes_use_their_own_lru_baseline():
    runs, _ = validate(ROOT)
    trace = next(t for t in sections(ROOT, runs) if t["trace"] == "mooncake-toolagent")
    record = runs["mooncake-toolagent", "claude"]
    tuning = record["selection"]["tuning"]
    assert trace["tables"][0]["rows"][0]["values"][1] == fmt(tuning["mean_ttft_ms"], 3)
    assert trace["tables"][1]["rows"][0]["values"][1] == fmt(
        record["evaluation"]["mean_ttft_ms"], 3
    )
    assert (
        trace["tables"][0]["rows"][0]["values"][1]
        != trace["tables"][1]["rows"][0]["values"][1]
    )
    assert delta(75, 50) == 50  # Relative percent, rather than 25 percentage points.
    assert delta(3, 0) is None  # Undefined comparisons must not invent a percentage.
