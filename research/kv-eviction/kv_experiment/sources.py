"""Immutable public data references; hashes describe the downloaded bytes."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class TraceSource:
    name: str
    revision: str
    url: str
    sha256: str
    filename: str
    kind: str
    block_size: int
    license: str
    requests: int | None = None
    subset_lines: int | None = None
    uncompressed_sha256: str | None = None

    def metadata(self) -> dict:
        result = asdict(self)
        result["license_url"] = (
            f"https://github.com/kvcache-ai/Mooncake/blob/{self.revision}/LICENSE-APACHE"
            if self.kind == "mooncake"
            else self.url.split("/resolve/")[0] + f"/blob/{self.revision}/README.md"
        )
        return result


MOONCAKE = "bd7526307049ee1612cfcb90daceffb128994ade"
MOONCAKE_URL = f"https://raw.githubusercontent.com/kvcache-ai/Mooncake/{MOONCAKE}/FAST25-release/traces"
AGENTX = "23f152f6f0f9399a85901b89a6458def0ef16729"
FREE = "16d3c04dae6ccaa0c48a9064e204e3a8f1eb9ade"
SOURCES = {
    "mooncake-toolagent": TraceSource(
        "mooncake-toolagent",
        MOONCAKE,
        f"{MOONCAKE_URL}/toolagent_trace.jsonl",
        "48a2db1a13d3bc05e6330140c64f604ba366df20d3c9e128b5c35a01c1fa5f71",
        "mooncake-toolagent.jsonl",
        "mooncake",
        512,
        "Apache-2.0",
        requests=23608,
    ),
    "mooncake-conversation": TraceSource(
        "mooncake-conversation",
        MOONCAKE,
        f"{MOONCAKE_URL}/conversation_trace.jsonl",
        "b8cbb061a85206d729d91cdc2981f43c9e0d99209dce588d3af5f7934408b9df",
        "mooncake-conversation.jsonl",
        "mooncake",
        512,
        "Apache-2.0",
        requests=12031,
    ),
    "agentx": TraceSource(
        "agentx",
        AGENTX,
        f"https://huggingface.co/datasets/semianalysisai/cc-traces-weka-062126/resolve/{AGENTX}/traces.jsonl",
        "26cc19dcee8ea765f93fa33062e2e5fe65aa86383c15730390839338ed762153",
        "agentx-first-16-sessions.jsonl",
        "weka",
        64,
        "Apache-2.0",
        subset_lines=16,
    ),
    "freeinference": TraceSource(
        "freeinference",
        FREE,
        f"https://huggingface.co/datasets/harvardMadsys/freeinference_agentic_trace/resolve/{FREE}/release/2026-05-17_2026-05-23/traces/2026-05-20.jsonl.zst",
        "ea26af1a19b0b33ee40d89f5007bfd67f66cf4ee574f8b8b2819b6b16865ec38",
        "freeinference-2026-05-20.jsonl.zst",
        "freeinference",
        16,
        "CC-BY-4.0",
        requests=383,
        uncompressed_sha256="adfe09d432bd8951ae8118af7f002da5c3ebc5c510c098cc35c10d2f1f71940b",
    ),
}
