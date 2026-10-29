//! Minimal single-worker SGLang replay using Dynamo's pinned AISimulate core.
use std::cell::RefCell;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::rc::Rc;
use std::sync::mpsc::{self, Receiver, Sender};
use std::time::Duration;

use aisimulate_core::engine::custom_eviction::{LeafCandidate, with_leaf_selector};
use aisimulate_core::engine::{Backend, KvEvictionPolicy};
use aisimulate_core::replay::{
    ReplayCaptureOptions, ReplayDeterminism, ReplayEngineConfig, ReplayEngineFactory,
    ReplaySpec, ReplayTopology, Replayer,
};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const CORE_VERSION: &str = "0.13.0-dev.202610160000000073";
const TIMEOUT: Duration = Duration::from_secs(5);

struct PolicyProcess {
    child: Child,
    requests: Sender<Vec<u8>>,
    replies: Receiver<std::io::Result<String>>,
}

impl PolicyProcess {
    fn start(python: &str, policy: &Path, expected_hash: &str) -> Result<Self> {
        let worker = Path::new(env!("CARGO_MANIFEST_DIR")).join("policy_worker.py");
        let mut child = Command::new(python)
            .args(["-I", "-B"])
            .arg(worker)
            .arg(policy.canonicalize()?)
            .env_remove("PYTHONPATH")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .context("start Python policy worker")?;
        let mut stdin = child.stdin.take().unwrap();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let (send, receive) = mpsc::channel::<Vec<u8>>();
        let (reply, replies) = mpsc::channel();
        // Both writes and reads run off the replay thread so a policy that stops
        // reading cannot evade the timeout by filling the input pipe.
        std::thread::spawn(move || {
            let read = |stdout: &mut BufReader<_>| {
                let mut line = String::new();
                stdout.read_line(&mut line).map(|_| line)
            };
            if reply.send(read(&mut stdout)).is_err() { return; }
            for payload in receive {
                let result = stdin.write_all(&payload)
                    .and_then(|_| stdin.flush())
                    .and_then(|_| read(&mut stdout));
                if reply.send(result).is_err() { break; }
            }
        });
        let process = Self { child, requests: send, replies };
        let ready = process.response()?;
        ensure!(ready["status"] == "ready", "policy startup failed: {ready}");
        ensure!(ready["source_sha256"] == expected_hash, "policy changed during startup");
        Ok(process)
    }

    fn response(&self) -> Result<Value> {
        let line = self.replies.recv_timeout(TIMEOUT)
            .context("policy timed out or disconnected")??;
        serde_json::from_str(&line).context("policy returned invalid JSON or exited")
    }

    fn select(&mut self, candidates: &[LeafCandidate]) -> Result<u64> {
        let mut payload = serde_json::to_vec(candidates)?;
        payload.push(b'\n');
        self.requests.send(payload).context("policy transport closed")?;
        let response = self.response()?;
        let victim = response["victim_id"].as_u64()
            .with_context(|| format!("invalid policy response: {response}"))?;
        ensure!(candidates.iter().any(|b| b.block_id == victim), "ineligible victim {victim}");
        Ok(victim)
    }
}

impl Drop for PolicyProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    ensure!(args.len() == 3 || args.len() == 4,
        "usage: kv-policy-replay SPEC.json POLICY.py|- [PYTHON_EXECUTABLE]");
    let input = std::fs::read(&args[1])?;
    let spec: ReplaySpec = serde_json::from_slice(&input).context("parse ReplaySpec")?;
    ensure!(matches!(&spec.topology, ReplayTopology::Aggregated { workers }
        if workers.initial_workers == 1), "research hook supports one aggregated worker only");
    let config: ReplayEngineConfig = serde_json::from_value(spec.engine.clone())?;
    ensure!(config.rank.backend == Backend::Sglang && config.dp_size == 1,
        "research hook requires SGLang with dp_size=1");
    ensure!(config.kv_eviction_policy == KvEvictionPolicy::Lru,
        "research hook cannot combine with a future-aware policy");
    ensure!(spec.requests.iter().all(|r| r.input_token_ids.is_some()),
        "explicit input token IDs required; length-only traces cannot establish shared prefixes");

    let decisions = Rc::new(RefCell::new(Vec::<Value>::new()));
    let run = || {
        Replayer::new(spec, ReplayEngineFactory::new())?
            .with_capture_options(ReplayCaptureOptions {
                capture_per_request: true,
                determinism: ReplayDeterminism::CanonicalV1,
                ..Default::default()
            })
            .run()
    };
    let custom = args[2] != "-";
    let policy_source = if custom { Some(std::fs::read(&args[2])?) } else { None };
    let report = if custom {
        let mut process = PolicyProcess::start(
            args.get(3).map_or("python3", String::as_str),
            Path::new(&args[2]),
            &digest(policy_source.as_ref().unwrap()),
        )?;
        let audit = Rc::clone(&decisions);
        with_leaf_selector(Box::new(move |candidates| {
            // Fail closed: invalid/timeout decisions abort the run, never score a fallback.
            let victim = process.select(candidates).unwrap_or_else(|error| panic!("{error:#}"));
            audit.borrow_mut().push(json!({"candidates": candidates, "victim_id": victim}));
            victim
        }), run)?
    } else {
        run()?
    };
    let mut summary = serde_json::to_value(&report)?;
    if custom { summary["kv_eviction_policy"] = json!("custom_python_leaf_v1"); }
    let result = json!({
        "schema_version": 1,
        "engine": "aisimulate-core",
        "engine_version": CORE_VERSION,
        "custom_hook_sha256": digest(include_bytes!("../custom_eviction.patch")),
        "spec_sha256": digest(&input),
        "replay_spec": serde_json::from_slice::<Value>(&input)?,
        "dependency_lock_sha256": digest(include_bytes!("../Cargo.lock")),
        "policy_worker_sha256": digest(include_bytes!("../policy_worker.py")),
        "policy_sha256": policy_source.as_ref().map(|source| digest(source)),
        "policy_source": policy_source.as_ref().map(|source| String::from_utf8_lossy(source)),
        "timing_model": config.rank.timing_model,
        "summary": summary,
        "per_request": report.per_request,
        // Attempts may include rolled-back admission transactions; do not treat
        // this diagnostic log as a count of committed cache evictions.
        "eviction_decision_attempts": &*decisions.borrow(),
    });
    serde_json::to_writer_pretty(std::io::stdout().lock(), &result)?;
    println!();
    Ok(())
}
