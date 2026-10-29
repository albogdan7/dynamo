// SPDX-License-Identifier: Apache-2.0
//! Single-worker native replay with causal inactive-block decisions over JSON pipes.
use std::fs::File;
use std::io::{self, BufRead, BufWriter, Write};
use std::path::Path;

use aisimulate_core::engine::{Backend, EngineConfig};
use aisimulate_core::replay::loadgen::Trace;
use aisimulate_core::replay::{
    ReplayCaptureOptions, ReplayDeterminism, ReplayEngineConfig, ReplayEngineFactory,
    ReplayRequest, ReplaySpec, ReplayTopology, Replayer,
};
use aisimulate_core::research_eviction::{self, Candidate};
use anyhow::{Context, Result, ensure};
use serde_json::{Value, json};

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
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
    let trace = Trace::from_mooncake(Path::new(&args[1]), rank.block_size)?;
    let requests = trace.to_single_turn_requests()?;
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
    match args[4].as_str() {
        "native-lru" => {}
        "policy" => research_eviction::set_callback(Box::new(|candidates: &[Candidate]| {
            let mut stdout = io::stdout().lock();
            serde_json::to_writer(&mut stdout, &json!({"candidates": candidates}))?;
            writeln!(stdout)?;
            stdout.flush()?;
            let mut line = String::new();
            io::stdin().lock().read_line(&mut line)?;
            let response: Value = serde_json::from_str(&line)?;
            response["victim_id"]
                .as_u64()
                .context("policy response missing victim_id")
        })),
        other => anyhow::bail!("unknown policy mode {other}"),
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
    let report = Replayer::new(spec, ReplayEngineFactory::new())?
        .with_capture_options(ReplayCaptureOptions {
            capture_per_request: true,
            determinism: ReplayDeterminism::CanonicalV1,
            ..Default::default()
        })
        .run()?;
    let mut writer = BufWriter::new(File::create(output.join("requests.jsonl"))?);
    for request in &report.per_request {
        serde_json::to_writer(&mut writer, request)?;
        writeln!(writer)?;
    }
    writer.flush()?;
    serde_json::to_writer_pretty(File::create(output.join("native-report.json"))?, &report)?;
    serde_json::to_writer_pretty(
        File::create(output.join("evictions.json"))?,
        &json!({"evictions":research_eviction::evictions()}),
    )?;
    println!("{{\"status\":\"done\"}}");
    Ok(())
}
