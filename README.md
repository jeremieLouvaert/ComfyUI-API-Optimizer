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

- **Hash Vault (Check Cache):** Hashes any combination of a prompt STRING and up to four `any_input` slots — wire an image, a converted-widget dropdown, a converted-widget float, whatever defines uniqueness for your API call. All inputs are optional; any subset you connect factors into the cache key. No prompt? Hash on image + widget values alone.
- **Lazy API Switch:** Uses ComfyUI's `{"lazy": True}` evaluation engine. On a cache hit, this switch **physically prevents** the upstream API node from executing — saving money and time.
- **Hash Vault (Save Result):** Writes new API outputs to the vault for future cache hits.

#### Key Features

- **Full-Content Tensor Hashing:** Hashes the complete byte representation of tensors (including dtype and shape metadata) — no lossy approximations.
- **Recursive Hashing:** Correctly handles nested data structures (dicts, lists, tuples) common in ComfyUI latents and conditioning.
- **Cache TTL:** Optional time-to-live for cache entries. Expired entries are automatically removed and treated as cache misses. Set to `0` for entries that never expire.
- **Device-Portable Caching:** All tensors are saved to CPU and loaded with `map_location="cpu"`, so cache files work regardless of GPU configuration.
- **Atomic Writes:** Cache files are written to a temp file first, then atomically replaced — preventing corruption from interrupted writes.
- **Concurrent-Safe:** File locking on every cache read/write operation.
- **Sidecar Metadata (v1.3.0):** Every saved entry gets a `{hash_key}.json` sidecar with human-readable `label`, `created_at`, `last_accessed_at`, and payload summary. Image outputs also get a `{hash_key}.thumb.png` 256px preview. Sidecar data is decoupled from the hash, so editing a `label` never invalidates cache. This is what the Hash Vault Browser indexes.
- **Hash Vault Browser (v1.4.0+):** A modal gallery over the entire vault. Three ways to open:
  1. **Keyboard**: `Ctrl+Shift+H` from anywhere in ComfyUI (added v1.4.1)
  2. **Sidebar**: click the `Hash Vault` tab in the left sidebar, then the `Open Browser` button (added v1.4.1)
  3. **Menu**: `Extensions → AKURATE: Hash Vault Browser` in the top menu bar

  Shows every cached entry as a card with thumbnail, label, relative age, and `.pt` size. Live substring filter on label + hash. Click a card to copy its hash to clipboard. Sorted by last-accessed desc. No load-into-workflow action yet — that's v1.5+.

### Sidecar Label Input

The Save Result node has an optional `label` input (v1.3.0+). Wire a human-readable string like `"Alec Soth / Songbook / full"` and the Hash Vault Browser will surface it when you need to find a specific past run. The label is stored in the sidecar JSON only, so changing it never breaks existing cache hits.

### Auto-labeling with the Label Builder node (v1.4.2)

Typing labels by hand gets old fast. The `🏷️ Hash Vault Label Builder` node concatenates up to four any-type inputs into one STRING, ready to wire into `Save Result`'s label input. Wire it once per workflow and never type a label again.

For the Gemini Style Transfer pattern:
- `in_1` ← Settings node's `style` output (e.g. `"Alec Soth"`)
- `in_2` ← Settings node's `variant` output (e.g. `"Niagara"`)
- `in_3` ← Settings node's `intensity` output (e.g. `"full"`)
- `separator` = `" / "`
- → outputs `"Alec Soth / Niagara / full"`

For a Prompt Studio → Gemini Image Generate pattern:
- `in_1` ← Prompt Studio's `style` output
- `in_2` ← the assembled prompt (first line, or a short identifier)
- → outputs `"Sebastião Salgado / Serra Pelada gold mine workers"`

Empty or None inputs are skipped, so partial wiring stays clean. Non-string inputs are stringified at join time.

### Migrating pre-v1.3.0 entries

Existing `.pt` files have no sidecar. To backfill:

```bash
# From the pack root, using the ComfyUI embedded Python:
python tools/migrate_hash_vault.py --dry-run       # preview
python tools/migrate_hash_vault.py                 # execute

# Override the vault location:
python tools/migrate_hash_vault.py --vault-dir "F:/path/to/output/hash_vault"
```

Orphan entries are labelled `(legacy)` by default and assigned `created_at` from file mtime.

## How to Use the Hash Vault

To properly bypass an API node, sandwich it with the vault nodes:

1. Connect your Prompt/Image to **Check Cache**.
2. Connect the `is_cached` output to the **Lazy API Switch**.
3. Connect your Prompt/Image to your actual API Node.
4. Connect the output of your API Node to **Save Result** (using the `hash_key` from step 1).
5. Connect both the `cached_data` (from step 1) and the `api_data` (from step 4) to the **Lazy API Switch**.

### What to feed Check Cache

Check Cache has one STRING socket (`payload_string`) and four any-type sockets (`any_input`, `any_input_2`, `any_input_3`, `any_input_4`). All are optional — connect whatever defines uniqueness for your API call:

- **Prompt-driven API (e.g. Gemini Image Generate):** wire the prompt STRING to `payload_string`. Done.
- **Image + prompt API (e.g. image edit):** prompt → `payload_string`, image → `any_input`.
- **Image-only API with widgets (e.g. Gemini Style Transfer — no prompt, but a style dropdown and strength float):** right-click the style widget → Convert Widget to Input, same for strength. Wire image → `any_input`, style → `any_input_2`, strength → `any_input_3`. All three factor into the hash; changing any of them produces a new cache key.

Any subset of slots works; unused slots contribute nothing to the hash. Adding or ignoring `any_input_2/3/4` in a new workflow doesn't invalidate existing cache keys from older workflows that only used `payload_string` + `any_input`.

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

## Example Workflows

### Prompt-driven API — [`workflows/hash_vault_basic.json`](workflows/hash_vault_basic.json)

The classic pattern for an API node whose uniqueness lives in a prompt STRING (e.g. Gemini Image Generate). One `StringConstantMultiline` feeds both Hash Vault's `payload_string` and the API node's prompt. Drag it in, set your Gemini API key, press Queue twice: first run generates and caches, second run returns the cached image with zero API call.

### Image-only API with widget inputs — [`workflows/hash_vault_image_only.json`](workflows/hash_vault_image_only.json)

Shows the v1.2.0 pattern for an API that has no prompt STRING but does have meaningful widgets, using Gemini Style Transfer (image + style dropdown + intensity dropdown). The Style Transfer Settings node owns the real dropdowns and emits `style` + `intensity` as STRING outputs. Each output fans out to BOTH the matching Style Transfer input (widget converted to input) AND a Hash Vault `any_input_N` slot. The image fans out to Style Transfer AND Hash Vault's `any_input`. All three factor into the hash; change any one of them → new cache key → API runs once.

The Settings node exists specifically to keep the dropdown UX. A raw STRING primitive driving a converted-widget input has no validation and no typeahead, so a typo silently breaks the cache (the hash is still valid, but Style Transfer errors on the bad value at runtime). The Settings node provides the same ComfyUI-native combo box the Style Transfer widget has, so the wired value is always a known-valid option.

### Requires

Both workflows use [ComfyUI-Gemini-Direct](https://github.com/jeremieLouvaert/ComfyUI-Gemini-Direct) as the API node. The prompt-driven workflow uses [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes)'s `StringConstantMultiline` as the prompt source; any STRING primitive works. The image-only workflow uses Gemini-Direct's own `Gemini Style Transfer Settings` node for the style + intensity dropdowns.

## Output Files

All data is stored under your ComfyUI output directory:

| Path | Description |
|------|-------------|
| `output/api_metrics/api_costs.json` | Current cost ledger (per-provider totals) |
| `output/api_metrics/api_transactions.jsonl` | Append-only audit log with timestamps |
| `output/api_metrics/api_costs_archive_*.json` | Archived ledgers from budget resets |
| `output/hash_vault/*.pt` | Cached API outputs (PyTorch format) |
| `output/hash_vault/*.json` | Sidecar metadata: label, timestamps, payload summary (v1.3.0+) |
| `output/hash_vault/*.thumb.png` | 256px preview for image outputs (v1.3.0+) |

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
