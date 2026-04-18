# ComfyUI API Optimizer

A suite of production-grade custom nodes for ComfyUI designed for workflows that rely on external remote APIs (Kling 3.0, Magnific, Banana.dev, RunPod, etc.).

When your compute moves to the cloud, your bottlenecks shift from VRAM limitations to **API Costs, Latency, and Serialization**. This custom node pack solves these problems natively inside ComfyUI.

## Included Nodes

### 1. API Cost & Quota Tracker

Acts as a circuit-breaker for your wallet. Pass your prompt or image through this node before it hits your API node.

- **Budget Enforcement:** Set a `$ Budget Limit` and `$ Cost Per Run`. A persistent ledger tracks all charges. If the next run would exceed the budget, execution halts *before* the API is charged.
- **Precise Arithmetic:** Uses `decimal.Decimal` internally — no floating-point drift on large batch runs.
- **Transaction Audit Log:** Every charge is appended to `api_transactions.jsonl` with timestamps for full traceability.
- **Safe Resets:** Resetting the budget archives the previous ledger instead of discarding it.
- **Concurrent-Safe:** File locking prevents corruption when multiple ComfyUI instances share the same output directory.

### 2. The Deterministic Hash Vault Suite (3 Nodes)

ComfyUI's native caching often breaks with external API nodes (dynamic timestamps, non-deterministic seeds). The Hash Vault is an aggressive disk-caching layer that strictly hashes your prompt, parameters, and input tensors.

- **Hash Vault (Check Cache):** Hashes your inputs and checks local disk for a cached result.
- **Lazy API Switch:** Uses ComfyUI's `{"lazy": True}` evaluation engine. On a cache hit, this switch **physically prevents** the upstream API node from executing — saving money and time.
- **Hash Vault (Save Result):** Writes new API outputs to the vault for future cache hits.

#### Key Features

- **Full-Content Tensor Hashing:** Hashes the complete byte representation of tensors (including dtype and shape metadata) — no lossy approximations.
- **Recursive Hashing:** Correctly handles nested data structures (dicts, lists, tuples) common in ComfyUI latents and conditioning.
- **Cache TTL:** Optional time-to-live for cache entries. Expired entries are automatically removed and treated as cache misses. Set to `0` for entries that never expire.
- **Device-Portable Caching:** All tensors are saved to CPU and loaded with `map_location="cpu"`, so cache files work regardless of GPU configuration.
- **Atomic Writes:** Cache files are written to a temp file first, then atomically replaced — preventing corruption from interrupted writes.
- **Concurrent-Safe:** File locking on every cache read/write operation.

## How to Use the Hash Vault

To properly bypass an API node, sandwich it with the vault nodes:

1. Connect your Prompt/Image to **Check Cache**.
2. Connect the `is_cached` output to the **Lazy API Switch**.
3. Connect your Prompt/Image to your actual API Node.
4. Connect the output of your API Node to **Save Result** (using the `hash_key` from step 1).
5. Connect both the `cached_data` (from step 1) and the `api_data` (from step 4) to the **Lazy API Switch**.

```
                           ┌─────────────┐
              ┌───────────►│  API Node    ├──► 💾 Save Result ──┐
              │            └─────────────┘                      │
 Prompt/Image─┤                                                 │
              │            ┌─────────────┐                      ▼
              └───────────►│ 🔍 Check    ├──────────────► 🔀 Lazy Switch ──► Output
                           │   Cache     │  is_cached           ▲
                           └──┬──────────┘                      │
                              │ cached_data ────────────────────┘
```

## Example Workflow

A ready-to-run wiring is in [`workflows/hash_vault_basic.json`](workflows/hash_vault_basic.json). Drag it into ComfyUI, set your Gemini API key (or swap the Gemini node for any other API generator), press Queue Prompt twice:

1. **First run** — Hash Vault reports a miss, Gemini generates, Save writes `output/hash_vault/<hash>.pt`.
2. **Second run (same prompt)** — Hash Vault reports a hit, Lazy Switch short-circuits the Gemini branch entirely, the cached image displays. No API call, no cost.

Change the prompt, queue again — new hash, new miss, new API call. Re-run the old prompt — still cached, still free.

The workflow uses [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes)'s `StringConstantMultiline` as the shared prompt source. Any STRING primitive works in its place; the key is that one source drives both Hash Vault and the API node so they hash the same input.

## Output Files

All data is stored under your ComfyUI output directory:

| Path | Description |
|------|-------------|
| `output/api_metrics/api_costs.json` | Current cost ledger (per-provider totals) |
| `output/api_metrics/api_transactions.jsonl` | Append-only audit log with timestamps |
| `output/api_metrics/api_costs_archive_*.json` | Archived ledgers from budget resets |
| `output/hash_vault/*.pt` | Cached API outputs (PyTorch format) |

## Installation

Clone this repository into your `ComfyUI/custom_nodes/` directory:

```bash
cd ComfyUI/custom_nodes/
git clone https://github.com/jeremieLouvaert/ComfyUI-API-Optimizer.git
pip install -r ComfyUI-API-Optimizer/requirements.txt
```

Restart ComfyUI.

### Dependencies

- **PyTorch** — already present in any ComfyUI installation
- **filelock** — typically already installed as a transitive dependency of PyTorch/HuggingFace. If not, `pip install filelock`.
