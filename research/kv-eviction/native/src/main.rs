// SPDX-License-Identifier: Apache-2.0
//! Single-worker native replay with causal inactive-block decisions over JSON pipes.
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{self, BufRead, BufWriter, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};

use aisimulate_core::engine::{Backend, EngineConfig};
use aisimulate_core::replay::loadgen::{Trace, WekaImporter, load_agentic_mooncake};
use aisimulate_core::replay::{
    ReplayCaptureOptions, ReplayDeterminism, ReplayEngineConfig, ReplayEngineFactory,
    ReplayRequest, ReplayRuntimeInput, ReplaySpec, ReplayTelemetryObserver,
    ReplayTelemetrySnapshot, ReplayTopology, Replayer,
};
use aisimulate_core::research_eviction::{self, Candidate, NativeOrder, Pause, RetentionPolicy};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};

/// Writes per-rank KV occupancy gauges sampled on the simulated clock.
/// Gauges are point-in-time; preemptions count the interval ending at the sample.
struct OccupancyWriter(Arc<Mutex<BufWriter<File>>>);

impl ReplayTelemetryObserver for OccupancyWriter {
    fn on_sample(&mut self, sample: ReplayTelemetrySnapshot) -> Result<()> {
        let ranks = sample
            .prefill_scheduler_metrics
            .iter()
            .chain(&sample.decode_scheduler_metrics)
            .map(|rank| {
                Ok(json!({
                    "worker_id": rank.worker_id,
                    "dp_rank": rank.dp_rank,
                    "active_blocks": rank.active_blocks,
                    "inactive_blocks": rank.inactive_blocks,
                    "free_blocks": rank.free_blocks.context("free-block telemetry requires a linear vLLM pool")?,
                    "total_blocks": rank.total_blocks,
                    "running_requests": rank.running_requests,
                    "waiting_requests": rank.waiting_requests,
                }))
            })
            .collect::<Result<Vec<_>>>()?;
        let mut writer = self.0.lock().unwrap();
        serde_json::to_writer(
            &mut *writer,
            &json!({
                "ordinal": sample.sample_ordinal,
                "kind": sample.kind,
                "interval_start_ms": sample.interval_start_ms,
                "sampled_at_ms": sample.sampled_at_ms,
                "preemptions": sample.prefill_interval_metrics.preemptions
                    + sample.decode_interval_metrics.preemptions,
                "router_pending_requests": sample.router_pending_prefill_requests
                    + sample.router_pending_decode_requests,
                "ranks": ranks,
            }),
        )?;
        writeln!(writer)?;
        Ok(())
    }
}

/// Synchronous JSON-lines exchange with the policy process over stdout/stdin.
fn ask(message: Value) -> Result<Value> {
    let mut stdout = io::stdout().lock();
    serde_json::to_writer(&mut stdout, &message)?;
    writeln!(stdout)?;
    stdout.flush()?;
    let mut line = String::new();
    io::stdin().lock().read_line(&mut line)?;
    Ok(serde_json::from_str(&line)?)
}

/// Native research baselines; absent configuration keeps the engine's LRU.
fn configure_research_policy(config: &Value, policy_process: bool) -> Result<bool> {
    let research = &config["research_policy"];
    let retention = &research["retention"];
    let name = retention["policy"].as_str().unwrap_or("none");
    let order = match research["order"].as_str().unwrap_or("lru") {
        _ if name != "none" => NativeOrder::Protected,
        "lru" => NativeOrder::Lru,
        "lfu" => NativeOrder::Lfu,
        "slru" => NativeOrder::Slru,
        "random" => NativeOrder::Random,
        other => anyhow::bail!("unknown research order {other}"),
    };
    research_eviction::set_native_order(order, research["seed"].as_u64().unwrap_or(0));
    let policy = match name {
        "none" => RetentionPolicy::None,
        "last_gap" => RetentionPolicy::LastGap,
        "global_median" => RetentionPolicy::GlobalMedian,
        "continuum" => {
            let reload: Vec<f64> = serde_json::from_value(retention["reload_quadratic"].clone())?;
            ensure!(reload.len() == 3, "reload_quadratic needs [a, b, c]");
            RetentionPolicy::Continuum {
                reload: [reload[0], reload[1], reload[2]],
                min_history: retention["min_history"].as_u64().context("min_history missing")?,
                cold_mean_ms: retention["cold_mean_ms"].as_f64().context("cold_mean_ms missing")?,
            }
        }
        "oracle" => RetentionPolicy::Oracle {
            gaps: serde_json::from_reader(File::open(
                retention["oracle_gaps"].as_str().context("oracle_gaps missing")?,
            )?)?,
            slack_ms: retention["slack_ms"].as_f64().context("slack_ms missing")?,
        },
        "callback" => {
            ensure!(policy_process, "callback retention needs a policy process");
            RetentionPolicy::Callback(Box::new(|pause: &Pause| {
                let response = ask(json!({"pause": pause}))?;
                match &response["protect_until_ms"] {
                    Value::Null => Ok(None),
                    value => Ok(Some(value.as_f64().context("protect_until_ms must be a number or null")?)),
                }
            }))
        }
        other => anyhow::bail!("unknown retention policy {other}"),
    };
    let callback = name == "callback";
    if name != "none" {
        research_eviction::set_retention(policy);
    }
    Ok(callback)
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    if args.get(1).is_some_and(|value| value == "import-weka") {
        ensure!(
            args.len() == 4,
            "usage: kv-eviction-replay import-weka INPUT OUTPUT"
        );
        let importer = WekaImporter::open(&args[2])?;
        let (summary, mut rows) = importer.collect_rows()?;
        let mut ids = BTreeMap::new();
        for row in &mut rows {
            // Preserve causal completion/spawn/join delays, without an authored
            // absolute floor that would suppress speedups from faster serving.
            if !row.dependencies.is_empty() {
                row.not_before_ms = 0.0;
            }
            for hash in row.hash_ids.as_mut().context("missing Weka hashes")? {
                let next = ids.len() as u64;
                *hash = *ids.entry((row.model.clone(), *hash)).or_insert(next);
            }
        }
        let mut writer = BufWriter::new(File::create(&args[3])?);
        let mut header = summary.header.clone();
        header.source.format = "weka-model-isolated-closed-loop-v1".into();
        serde_json::to_writer(&mut writer, &header)?;
        writeln!(writer)?;
        for row in &rows {
            serde_json::to_writer(&mut writer, row)?;
            writeln!(writer)?;
        }
        writer.flush()?;
        serde_json::to_writer_pretty(
            File::create(Path::new(&args[3]).with_extension("import.json"))?,
            &json!({"requests":summary.requests,"plays":summary.plays,
                "nested_timestamp_basis":summary.nested_timestamp_basis.as_str(),
                "model_isolated_hashes":true,"descendant_authored_time_floors":false}),
        )?;
        return Ok(());
    }
    ensure!(
        args.len() == 5,
        "usage: kv-eviction-replay TRACE CONFIG OUTPUT POLICY"
    );
    let output = Path::new(&args[3]);
    let config: Value = serde_json::from_reader(File::open(&args[2])?)?;
    let mut rank_value = serde_json::to_value(EngineConfig::for_backend(Backend::Vllm))?;
    rank_value
        .as_object_mut()
        .unwrap()
        .extend(config["rank"].as_object().context("rank missing")?.clone());
    let rank: EngineConfig = serde_json::from_value(rank_value)?;
    ensure!(
        rank.backend == Backend::Vllm,
        "research callback supports vLLM only"
    );
    let trace_options = &config["trace"];
    let source_block_size = trace_options["source_block_size"]
        .as_u64()
        .unwrap_or(rank.block_size as u64) as usize;
    let agentic = trace_options["format"].as_str() == Some("agentic_mooncake");
    let runtime_input = if agentic {
        let graph = load_agentic_mooncake(Path::new(&args[1]), source_block_size)?;
        let lanes = trace_options["agentic_lanes"]
            .as_u64()
            .map(|value| value as usize);
        Some(ReplayRuntimeInput::Workload(
            graph.into_trace_driver_with_options(rank.block_size, true, lanes)?,
        ))
    } else {
        None
    };
    let requests = if agentic {
        Vec::new()
    } else {
        Trace::from_mooncake(Path::new(&args[1]), source_block_size)?.to_single_turn_requests()?
    };
    let origin = requests
        .first()
        .and_then(|r| r.arrival_timestamp_ms)
        .unwrap_or(0.0);
    let requests = requests
        .into_iter()
        .enumerate()
        .map(|(i, r)| ReplayRequest {
            id: i.to_string(),
            arrival_time_ms: r.arrival_timestamp_ms.unwrap_or(0.0) - origin,
            input_tokens: r.tokens.len(),
            input_token_ids: Some(r.tokens),
            output_tokens: r.max_output_tokens,
            output_token_ids: r.output_token_ids,
            dp_rank: None,
            prefill_dp_rank: None,
            session_id: None,
            turn_index: None,
            metadata: Value::Null,
        })
        .collect();
    research_eviction::capture_tokens(output.join("tokens.jsonl"))?;
    let policy_process = match args[4].as_str() {
        "native-lru" => false,
        "policy" => true,
        other => anyhow::bail!("unknown policy mode {other}"),
    };
    let retention_callback = configure_research_policy(&config, policy_process)?;
    if policy_process && !retention_callback {
        research_eviction::set_callback(Box::new(|candidates: &[Candidate]| {
            ask(json!({"candidates": candidates}))?["victim_id"]
                .as_u64()
                .context("policy response missing victim_id")
        }));
    }
    let spec = ReplaySpec {
        version: 1,
        topology: ReplayTopology::aggregated(1),
        engine: serde_json::to_value(ReplayEngineConfig {
            rank,
            num_gpu_blocks_is_explicit: Some(true),
            ..Default::default()
        })?,
        adapters: Default::default(),
        max_sim_time_ms: None,
        max_in_flight: None,
        record_per_request: true,
        sla: Default::default(),
        requests,
    };
    let mut replayer = Replayer::new(spec, ReplayEngineFactory::new())?.with_capture_options(
        ReplayCaptureOptions {
            capture_per_request: true,
            determinism: ReplayDeterminism::CanonicalV1,
            ..Default::default()
        },
    );
    if let Some(input) = runtime_input {
        replayer = replayer.with_runtime_input(input);
    }
    // Telemetry is optional so instrumented and uninstrumented runs can be compared.
    let occupancy = match config["telemetry"]["sample_interval_ms"].as_f64() {
        Some(interval_ms) => {
            let writer = Arc::new(Mutex::new(BufWriter::new(File::create(
                output.join("occupancy.jsonl"),
            )?)));
            replayer = replayer
                .with_telemetry_observer(interval_ms, Box::new(OccupancyWriter(writer.clone())))?;
            Some(writer)
        }
        None => None,
    };
    let report = replayer.run()?;
    research_eviction::finish_tokens()?;
    if let Some(writer) = occupancy {
        writer.lock().unwrap().flush()?;
    }
    let mut writer = BufWriter::new(File::create(output.join("requests.jsonl"))?);
    for request in &report.per_request {
        serde_json::to_writer(&mut writer, request)?;
        writeln!(writer)?;
    }
    writer.flush()?;
    serde_json::to_writer_pretty(File::create(output.join("native-report.json"))?, &report)?;
    serde_json::to_writer_pretty(
        File::create(output.join("evictions.json"))?,
        &json!({"evictions":research_eviction::evictions(),
            "protected_evictions":research_eviction::protected_evictions()}),
    )?;
    println!("{{\"status\":\"done\"}}");
    Ok(())
}
