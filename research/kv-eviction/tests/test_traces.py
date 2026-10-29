"""Check prefix identity and timing validation before native replay."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_experiment.traces import validate


@pytest.mark.parametrize(
    "row",
    [
        {"timestamp": -1, "input_length": 512, "output_length": 1, "hash_ids": [1]},
        {
            "timestamp": float("nan"),
            "input_length": 512,
            "output_length": 1,
            "hash_ids": [1],
        },
        {"timestamp": 0, "input_length": 513, "output_length": 1, "hash_ids": [1]},
        {"timestamp": 0, "input_length": 512, "output_length": -1, "hash_ids": [1]},
    ],
)
def test_trace_rejects_invalid_rows(row):
    with pytest.raises(ValueError):
        validate([row])


def test_prefix_ancestry_is_validated():
    rows = [
        {"timestamp": 0, "input_length": 1024, "output_length": 1, "hash_ids": [1, 3]},
        {"timestamp": 1, "input_length": 1024, "output_length": 1, "hash_ids": [2, 3]},
    ]
    with pytest.raises(ValueError, match="ancestors"):
        validate(rows)
