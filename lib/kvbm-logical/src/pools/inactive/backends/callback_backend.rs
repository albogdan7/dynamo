// SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Callback-driven inactive-pool backend.
//!
//! Delegates eviction ordering to a caller-supplied [`EvictionCallback`],
//! enabling research experiments and simulation hooks without modifying
//! core KVBM logic.

use std::sync::Arc;
use std::time::Instant;

use crate::BlockId;
use crate::blocks::SequenceHash;
use crate::pools::store::InactiveIndex;

/// Metadata exposed to an eviction callback for one inactive block.
#[derive(Debug, Clone)]
pub struct BlockCandidate {
    /// Physical block slot identifier.
    pub block_id: BlockId,
    /// Content-addressed sequence hash encoding position and lineage.
    pub seq_hash: SequenceHash,
    /// Simulated or wall-clock time the block entered the inactive pool.
    pub inserted_at: Instant,
}

/// A callback that selects which blocks to evict.
///
/// Receives the current set of inactive [`BlockCandidate`]s and returns
/// a list of indices into that slice, ordered from first-to-evict to
/// last-to-evict. The backend will evict the first `requested_count`
/// valid indices returned. Indices that are out of range or duplicated
/// are silently skipped.
pub type EvictionCallback =
    Arc<dyn Fn(&[BlockCandidate], usize) -> Vec<usize> + Send + Sync>;

/// An inactive-pool backend that delegates eviction ordering to an
/// [`EvictionCallback`].
///
/// Use this to plug in a custom eviction policy at runtime — for example,
/// a Python function (via PyO3), an external process, or a research
/// algorithm — without modifying the core KVBM eviction logic.
pub struct CallbackBackend {
    blocks: Vec<(SequenceHash, BlockId, Instant)>,
    callback: EvictionCallback,
}

impl CallbackBackend {
    /// Construct a backend that will call `callback` on every eviction.
    pub fn new(callback: EvictionCallback) -> Self {
        Self {
            blocks: Vec::new(),
            callback,
        }
    }
}

impl InactiveIndex for CallbackBackend {
    fn find_matches(
        &mut self,
        hashes: &[SequenceHash],
        _touch: bool,
    ) -> Vec<(SequenceHash, BlockId)> {
        let mut matches = Vec::new();
        for &hash in hashes {
            if let Some(pos) = self.blocks.iter().position(|(h, _, _)| *h == hash) {
                let (h, id, _) = self.blocks.swap_remove(pos);
                matches.push((h, id));
            } else {
                break;
            }
        }
        matches
    }

    fn find_match(&mut self, hash: SequenceHash, _touch: bool) -> Option<(SequenceHash, BlockId)> {
        let pos = self.blocks.iter().position(|(h, _, _)| *h == hash)?;
        let (h, id, _) = self.blocks.swap_remove(pos);
        Some((h, id))
    }

    fn scan_matches(
        &mut self,
        hashes: &[SequenceHash],
        _touch: bool,
    ) -> Vec<(SequenceHash, BlockId)> {
        let mut matches = Vec::new();
        for &hash in hashes {
            if let Some(pos) = self.blocks.iter().position(|(h, _, _)| *h == hash) {
                let (h, id, _) = self.blocks.swap_remove(pos);
                matches.push((h, id));
            }
        }
        matches
    }

    fn allocate(&mut self, count: usize) -> Vec<(SequenceHash, BlockId)> {
        if count == 0 || self.blocks.is_empty() {
            return Vec::new();
        }
        let candidates: Vec<BlockCandidate> = self
            .blocks
            .iter()
            .map(|&(seq_hash, block_id, inserted_at)| BlockCandidate {
                block_id,
                seq_hash,
                inserted_at,
            })
            .collect();

        let mut indices = (self.callback)(&candidates, count);

        // Take the first `count` unique valid indices, sorted descending so
        // that swap_remove at a higher index never invalidates a lower one.
        indices.truncate(count);
        indices.sort_unstable_by(|a, b| b.cmp(a));
        indices.dedup();

        let mut out = Vec::with_capacity(indices.len());
        for idx in indices {
            if idx < self.blocks.len() {
                let (hash, id, _) = self.blocks.swap_remove(idx);
                out.push((hash, id));
            }
        }
        out
    }

    fn insert(&mut self, seq_hash: SequenceHash, block_id: BlockId) {
        self.blocks.push((seq_hash, block_id, Instant::now()));
    }

    fn len(&self) -> usize {
        self.blocks.len()
    }

    fn has(&self, seq_hash: SequenceHash) -> bool {
        self.blocks.iter().any(|(h, _, _)| *h == seq_hash)
    }

    fn take(&mut self, seq_hash: SequenceHash, block_id: BlockId) -> bool {
        if let Some(pos) = self
            .blocks
            .iter()
            .position(|(h, id, _)| *h == seq_hash && *id == block_id)
        {
            self.blocks.swap_remove(pos);
            true
        } else {
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::{block_id_and_hash, tokens_for_id};

    fn lru_callback() -> EvictionCallback {
        // Evict oldest-inserted first (index 0 = inserted earliest).
        Arc::new(|candidates: &[BlockCandidate], count: usize| -> Vec<usize> {
            let mut order: Vec<usize> = (0..candidates.len()).collect();
            order.sort_by_key(|&i| candidates[i].inserted_at);
            order.truncate(count);
            order
        })
    }

    #[test]
    fn test_callback_fifo_eviction() {
        let mut backend = CallbackBackend::new(lru_callback());

        let (id1, hash1) = block_id_and_hash(1, &tokens_for_id(1));
        let (id2, hash2) = block_id_and_hash(2, &tokens_for_id(2));
        let (id3, hash3) = block_id_and_hash(3, &tokens_for_id(3));

        backend.insert(hash1, id1);
        backend.insert(hash2, id2);
        backend.insert(hash3, id3);

        assert_eq!(backend.len(), 3);

        let evicted = backend.allocate(1);
        assert_eq!(evicted.len(), 1);
        assert_eq!(evicted[0].1, id1, "oldest block should be evicted first");
        assert_eq!(backend.len(), 2);
    }

    #[test]
    fn test_callback_allocate_more_than_available() {
        let mut backend = CallbackBackend::new(lru_callback());

        let (id1, hash1) = block_id_and_hash(1, &tokens_for_id(1));
        backend.insert(hash1, id1);

        let evicted = backend.allocate(5);
        assert_eq!(evicted.len(), 1);
        assert_eq!(backend.len(), 0);
    }

    #[test]
    fn test_callback_has_and_take() {
        let mut backend = CallbackBackend::new(lru_callback());

        let (id1, hash1) = block_id_and_hash(1, &tokens_for_id(1));
        backend.insert(hash1, id1);

        assert!(backend.has(hash1));
        assert!(backend.take(hash1, id1));
        assert!(!backend.has(hash1));
        assert_eq!(backend.len(), 0);
    }

    #[test]
    fn test_callback_find_match() {
        let mut backend = CallbackBackend::new(lru_callback());

        let (id1, hash1) = block_id_and_hash(1, &tokens_for_id(1));
        let (id2, hash2) = block_id_and_hash(2, &tokens_for_id(2));
        backend.insert(hash1, id1);
        backend.insert(hash2, id2);

        let found = backend.find_match(hash1, false);
        assert_eq!(found, Some((hash1, id1)));
        assert_eq!(backend.len(), 1);
        assert!(!backend.has(hash1));
        assert!(backend.has(hash2));
    }
}
