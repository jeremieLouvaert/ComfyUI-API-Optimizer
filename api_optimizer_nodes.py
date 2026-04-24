import os
import json
import hashlib
import time
import torch
from decimal import Decimal
from datetime import datetime, timezone

try:
    import folder_paths
except ImportError:
    # Allow this module to be imported outside ComfyUI (e.g. by tools/migrate_hash_vault.py)
    # so the sidecar helpers remain reusable. Node methods still require ComfyUI to run.
    folder_paths = None

try:
    from filelock import FileLock
except ImportError:
    # filelock is typically available in ComfyUI environments (transitive dep of torch).
    # This no-op fallback ensures nodes still work without it, just without concurrency safety.
    class FileLock:
        def __init__(self, lock_file, timeout=-1):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# ------------------------------------------------------------------------
# UTILITIES
# ------------------------------------------------------------------------
class AnyType(str):
    """Universal type that passes ComfyUI's type-checking by never reporting inequality."""
    def __ne__(self, __value: object) -> bool:
        return False

any_type = AnyType("*")

# ------------------------------------------------------------------------
# SIDECAR METADATA HELPERS (v1.3.0)
# Every vault entry has a .json sidecar + optional .thumb.png alongside the .pt.
# This is what the Hash Vault Browser reads; .pt itself is opaque and expensive to load.
# ------------------------------------------------------------------------

THUMBNAIL_MAX_SIZE = 256
SIDECAR_SUFFIX = ".json"
THUMBNAIL_SUFFIX = ".thumb.png"


def _find_image_tensor(data):
    """Walk an arbitrary api_output and return the first tensor that looks like a ComfyUI image.

    ComfyUI image convention: float tensor of shape [B, H, W, C] with C in {1, 3, 4}, values in [0, 1].
    Returns None if nothing qualifies.
    """
    if isinstance(data, torch.Tensor):
        t = data
        if t.ndim == 4 and t.shape[-1] in (1, 3, 4):
            return t
        if t.ndim == 3 and t.shape[-1] in (1, 3, 4):
            return t
        return None
    if isinstance(data, dict):
        # Skip "samples" (latent) — not a visible image
        for key, value in data.items():
            if key == "samples":
                continue
            found = _find_image_tensor(value)
            if found is not None:
                return found
        return None
    if isinstance(data, (list, tuple)):
        for item in data:
            found = _find_image_tensor(item)
            if found is not None:
                return found
        return None
    return None


def _generate_thumbnail(tensor, output_path):
    """Write a thumbnail PNG for an image tensor. Returns True on success, False otherwise."""
    if not _PIL_AVAILABLE:
        return False
    try:
        t = tensor
        if t.ndim == 4:
            t = t[0]  # first batch item
        if t.ndim != 3 or t.shape[-1] not in (1, 3, 4):
            return False

        arr = t.detach().cpu().clamp(0, 1).float().numpy()
        arr = (arr * 255.0).round().astype("uint8")

        if arr.shape[-1] == 1:
            arr = arr.squeeze(-1)
            mode = "L"
        elif arr.shape[-1] == 3:
            mode = "RGB"
        else:
            mode = "RGBA"

        img = Image.fromarray(arr, mode=mode)
        img.thumbnail((THUMBNAIL_MAX_SIZE, THUMBNAIL_MAX_SIZE), Image.LANCZOS)

        tmp_path = output_path + ".tmp"
        img.save(tmp_path, "PNG", optimize=True)
        os.replace(tmp_path, output_path)
        return True
    except Exception as e:
        print(f"⚠️ [API Vault] Thumbnail generation failed: {e}")
        return False


def _summarize_payload(data):
    """Produce a lightweight JSON-safe description of api_output for the browser."""
    if isinstance(data, torch.Tensor):
        return {
            "kind": "tensor",
            "shape": list(data.shape),
            "dtype": str(data.dtype).replace("torch.", ""),
        }
    if isinstance(data, dict):
        return {
            "kind": "dict",
            "keys": sorted([str(k) for k in data.keys()])[:16],
            "size": len(data),
        }
    if isinstance(data, (list, tuple)):
        return {
            "kind": type(data).__name__,
            "length": len(data),
            "first_item": _summarize_payload(data[0]) if len(data) > 0 else None,
        }
    return {"kind": type(data).__name__}


def _write_sidecar(vault_dir, hash_key, cpu_data, label):
    """Write {hash_key}.json (+ optional {hash_key}.thumb.png). Caller must already hold the vault lock."""
    sidecar_path = os.path.join(vault_dir, f"{hash_key}{SIDECAR_SUFFIX}")
    thumbnail_path = os.path.join(vault_dir, f"{hash_key}{THUMBNAIL_SUFFIX}")
    now_iso = datetime.now(timezone.utc).isoformat()

    image_tensor = _find_image_tensor(cpu_data)
    has_thumbnail = False
    if image_tensor is not None:
        has_thumbnail = _generate_thumbnail(image_tensor, thumbnail_path)

    sidecar = {
        "hash_key": hash_key,
        "label": (label or "").strip(),
        "created_at": now_iso,
        "last_accessed_at": now_iso,
        "payload": _summarize_payload(cpu_data),
        "thumbnail": f"{hash_key}{THUMBNAIL_SUFFIX}" if has_thumbnail else None,
        "schema_version": 1,
    }

    tmp_path = sidecar_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2)
    os.replace(tmp_path, sidecar_path)


def _touch_sidecar(vault_dir, hash_key):
    """Bump last_accessed_at on cache hit. Silent no-op if the sidecar is missing (pre-migration entry)."""
    sidecar_path = os.path.join(vault_dir, f"{hash_key}{SIDECAR_SUFFIX}")
    if not os.path.exists(sidecar_path):
        return
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            sidecar = json.load(f)
        sidecar["last_accessed_at"] = datetime.now(timezone.utc).isoformat()
        tmp_path = sidecar_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2)
        os.replace(tmp_path, sidecar_path)
    except Exception as e:
        # Sidecar update failure must never break a cache hit
        print(f"⚠️ [API Vault] Failed to update sidecar timestamp for {hash_key[:8]}: {e}")


# ------------------------------------------------------------------------
# NODE 1: API Cost & Quota Tracker
# ------------------------------------------------------------------------
class APICostTracker:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "passthrough": (any_type,),
                "api_provider": ("STRING", {"default": "Kling 3.0"}),
                "cost_per_run": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 1000.0, "step": 0.001}),
                "budget_limit": ("FLOAT", {"default": 10.0, "min": 0.0, "max": 10000.0, "step": 0.1}),
                "reset_budget": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = (any_type, "STRING")
    RETURN_NAMES = ("passthrough", "cost_summary")
    FUNCTION = "track_cost"
    CATEGORY = "API Optimization"

    def track_cost(self, passthrough, api_provider, cost_per_run, budget_limit, reset_budget):
        # Convert floats to Decimal at the boundary for precise financial arithmetic
        cost = Decimal(str(cost_per_run))
        limit = Decimal(str(budget_limit))

        if cost > limit:
            raise ValueError(
                f"[API Cost Tracker] cost_per_run (${cost}) exceeds budget_limit (${limit}). "
                f"Fix your node configuration."
            )

        log_dir = os.path.join(folder_paths.get_output_directory(), "api_metrics")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "api_costs.json")
        lock_file = log_file + ".lock"
        tx_log_file = os.path.join(log_dir, "api_transactions.jsonl")

        with FileLock(lock_file, timeout=10):
            costs = {}

            if os.path.exists(log_file) and not reset_budget:
                try:
                    with open(log_file, "r") as f:
                        raw = json.load(f)
                    costs = {k: Decimal(str(v)) for k, v in raw.items()}
                except json.JSONDecodeError as e:
                    print(f"⚠️ [API Cost Tracker] Corrupted ledger, backing up and resetting. Error: {e}")
                    backup_path = log_file + f".corrupt.{int(time.time())}"
                    os.replace(log_file, backup_path)
                    costs = {}
                except (OSError, IOError) as e:
                    print(f"⚠️ [API Cost Tracker] Failed to read ledger: {e}")
                    costs = {}

            if reset_budget:
                if costs:
                    archive_path = os.path.join(log_dir, f"api_costs_archive_{int(time.time())}.json")
                    with open(archive_path, "w") as f:
                        json.dump({k: str(v) for k, v in costs.items()}, f, indent=4)
                    print(f"📋 [API Cost Tracker] Budget reset. Previous ledger archived.")
                costs = {}

            current_total = sum(costs.values(), Decimal("0"))

            # CIRCUIT BREAKER: Stop execution before the API is charged
            if current_total + cost > limit:
                remaining = limit - current_total
                raise ValueError(
                    f"\n[🛑 API Cost Tracker] BUDGET EXCEEDED!\n"
                    f"Budget Limit: ${limit}\n"
                    f"Total Spent:  ${current_total}\n"
                    f"This Run:     ${cost}\n"
                    f"Remaining:    ${remaining}\n"
                    f"Halting execution to prevent unauthorized API charges."
                )

            # Update ledger
            if api_provider not in costs:
                costs[api_provider] = Decimal("0")
            costs[api_provider] += cost

            # Atomic write: temp file then replace
            tmp_file = log_file + ".tmp"
            with open(tmp_file, "w") as f:
                json.dump({k: str(v) for k, v in costs.items()}, f, indent=4)
            os.replace(tmp_file, log_file)

            # Append to transaction audit log
            new_total = sum(costs.values(), Decimal("0"))
            tx = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": api_provider,
                "cost": str(cost),
                "running_total": str(new_total),
            }
            with open(tx_log_file, "a") as f:
                f.write(json.dumps(tx) + "\n")

        remaining = limit - new_total
        summary = f"Total: ${new_total} | Remaining: ${remaining}"
        print(f"💰 [API Cost Tracker] Billed ${cost} to {api_provider}. {summary}")

        return (passthrough, summary)

# ------------------------------------------------------------------------
# NODE 2: Deterministic Hash Vault (Check Cache)
# ------------------------------------------------------------------------
class DeterministicHashVault:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "optional": {
                "payload_string": ("STRING", {"forceInput": True,
                                               "tooltip": "Prompt, JSON params, or any STRING that should factor into the cache key. Optional — you can hash purely on any_input slots if you prefer."}),
                "any_input":    (any_type, {"tooltip": "Any input to hash: image, latent, conditioning, or a converted widget (convert a downstream node's widget to input and wire it here). Content is hashed recursively."}),
                "any_input_2":  (any_type, {"tooltip": "Second any-type input. Use one slot per widget you want to factor into the cache key — e.g. image on any_input, style dropdown on any_input_2, strength float on any_input_3."}),
                "any_input_3":  (any_type, {"tooltip": "Third any-type input."}),
                "any_input_4":  (any_type, {"tooltip": "Fourth any-type input."}),
                "cache_ttl_hours": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 8760.0, "step": 1.0,
                                               "tooltip": "Cache time-to-live in hours. 0 = never expires"}),
            }
        }

    RETURN_TYPES = (any_type, "INT", "STRING")
    RETURN_NAMES = ("cached_data", "is_cached", "hash_key")
    FUNCTION = "check_vault"
    CATEGORY = "API Optimization"

    def _hash_tensor(self, hash_obj, tensor):
        """Hash full tensor content deterministically including dtype and shape metadata."""
        t = tensor.contiguous().cpu()
        hash_obj.update(f"__tensor:{t.dtype}:{list(t.shape)}:".encode("utf-8"))
        try:
            hash_obj.update(t.numpy().tobytes())
        except Exception:
            # Fallback for non-standard tensor types that can't convert to numpy
            hash_obj.update(bytes(t.untyped_storage()))

    def _hash_value(self, hash_obj, value):
        """Recursively hash any value — handles tensors, dicts, lists, and primitives."""
        if isinstance(value, torch.Tensor):
            self._hash_tensor(hash_obj, value)
        elif isinstance(value, dict):
            hash_obj.update(b"__dict:")
            for key in sorted(value.keys()):
                hash_obj.update(str(key).encode("utf-8"))
                self._hash_value(hash_obj, value[key])
        elif isinstance(value, (list, tuple)):
            hash_obj.update(b"__seq:")
            for item in value:
                self._hash_value(hash_obj, item)
        else:
            hash_obj.update(str(value).encode("utf-8"))

    def check_vault(self, payload_string="", any_input=None,
                    any_input_2=None, any_input_3=None, any_input_4=None,
                    cache_ttl_hours=0.0):
        hash_obj = hashlib.sha256()

        # payload_string is optional as of v1.2.0; an empty string still
        # contributes a stable (empty) marker so pre-v1.2 workflows that
        # explicitly passed "" as the payload produce the same hash they
        # used to.
        hash_obj.update(str(payload_string or "").encode("utf-8"))

        # any_input hashing is bit-identical to v1.0/v1.1 to preserve existing
        # cache keys — unchanged workflows keep hitting their existing entries.
        if any_input is not None:
            self._hash_value(hash_obj, any_input)

        # New slots introduced in v1.2.0. The "__slotN:" prefix only appears
        # when the slot is actually wired, so adding these inputs to the node
        # doesn't perturb hashes for workflows that only use any_input.
        for slot_idx, value in enumerate((any_input_2, any_input_3, any_input_4), start=2):
            if value is not None:
                hash_obj.update(f"__slot{slot_idx}:".encode("utf-8"))
                self._hash_value(hash_obj, value)

        hash_key = hash_obj.hexdigest()

        vault_dir = os.path.join(folder_paths.get_output_directory(), "hash_vault")
        os.makedirs(vault_dir, exist_ok=True)
        file_path = os.path.join(vault_dir, f"{hash_key}.pt")
        lock_path = file_path + ".lock"

        with FileLock(lock_path, timeout=10):
            if os.path.exists(file_path):
                # Check TTL if set
                if cache_ttl_hours > 0:
                    file_age_hours = (time.time() - os.path.getmtime(file_path)) / 3600
                    if file_age_hours > cache_ttl_hours:
                        print(
                            f"⏰ [API Vault] Cache expired for {hash_key[:8]} "
                            f"(age: {file_age_hours:.1f}h > TTL: {cache_ttl_hours:.1f}h)"
                        )
                        os.remove(file_path)
                        return (None, 0, hash_key)

                try:
                    # weights_only=False is required because cache may contain dicts/lists
                    # alongside tensors.  Safe here — we only load files our own save node wrote.
                    cached_data = torch.load(file_path, map_location="cpu", weights_only=False)
                    _touch_sidecar(vault_dir, hash_key)
                    print(f"🟢 [API Vault] Cache Hit! Hash: {hash_key[:8]}")
                    return (cached_data, 1, hash_key)
                except Exception as e:
                    print(f"⚠️ [API Vault] Corrupted cache for {hash_key[:8]}, removing. Error: {e}")
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass

        print(f"🔴 [API Vault] Cache Miss! Hash: {hash_key[:8]}")
        return (None, 0, hash_key)

# ------------------------------------------------------------------------
# NODE 3: Hash Vault (Save API Result)
# ------------------------------------------------------------------------
class HashVaultSave:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "hash_key": ("STRING", {"forceInput": True}),
                "api_output": (any_type,),
            },
            "optional": {
                "label": ("STRING", {
                    "default": "",
                    "multiline": False,
                    "tooltip": "Human-readable label for the Hash Vault Browser (e.g. 'Alec Soth / Songbook / full'). Written to sidecar metadata only — does NOT factor into the hash, so editing this never invalidates cache."
                }),
            }
        }

    RETURN_TYPES = (any_type,)
    RETURN_NAMES = ("api_output",)
    FUNCTION = "save_to_vault"
    CATEGORY = "API Optimization"

    # NOTE: OUTPUT_NODE must be False. We only want this to execute if the Switch demands it!

    def _to_cpu(self, data):
        """Recursively move all tensors to CPU for portable serialization."""
        if isinstance(data, torch.Tensor):
            return data.cpu()
        elif isinstance(data, dict):
            return {k: self._to_cpu(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._to_cpu(item) for item in data]
        elif isinstance(data, tuple):
            return tuple(self._to_cpu(item) for item in data)
        return data

    def save_to_vault(self, hash_key, api_output, label=""):
        vault_dir = os.path.join(folder_paths.get_output_directory(), "hash_vault")
        os.makedirs(vault_dir, exist_ok=True)
        file_path = os.path.join(vault_dir, f"{hash_key}.pt")
        lock_path = file_path + ".lock"
        tmp_path = file_path + ".tmp"

        # Move to CPU for portable serialization
        cpu_data = self._to_cpu(api_output)

        with FileLock(lock_path, timeout=10):
            # Atomic write: save to temp then replace
            torch.save(cpu_data, tmp_path)
            os.replace(tmp_path, file_path)

            # Sidecar + thumbnail (v1.3.0) — keep inside the lock so readers
            # see a consistent (.pt, .json, .thumb.png) triple.
            _write_sidecar(vault_dir, hash_key, cpu_data, label)

        label_suffix = f" [{label}]" if label else ""
        print(f"💾 [API Vault] Saved API output to Vault: {hash_key[:8]}{label_suffix}")
        return (api_output,)

# ------------------------------------------------------------------------
# NODE 4: Lazy API Switch (The Bypass Engine)
# ------------------------------------------------------------------------
class LazyAPISwitch:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "is_cached": ("INT",),
            },
            "optional": {
                # {"lazy": True} is the magic engine feature. ComfyUI will NOT execute
                # upstream nodes connected to these sockets unless check_lazy_status demands it.
                "cached_data": (any_type, {"lazy": True}),
                "api_data": (any_type, {"lazy": True}),
            }
        }

    RETURN_TYPES = (any_type,)
    RETURN_NAMES = ("final_output",)
    FUNCTION = "switch"
    CATEGORY = "API Optimization"

    def check_lazy_status(self, is_cached, cached_data=None, api_data=None):
        """Tell ComfyUI which inputs to actually evaluate.

        If cache hit  → demand only cached_data, leaving the API branch completely idle.
        If cache miss → demand only api_data, triggering the API execution branch.
        """
        if is_cached == 1:
            if cached_data is None:
                return ["cached_data"]
        else:
            if api_data is None:
                return ["api_data"]
        return []

    def switch(self, is_cached, cached_data=None, api_data=None):
        if is_cached == 1:
            print("🔀 [Lazy Switch] Cache Hit routed! API execution completely bypassed.")
            return (cached_data,)
        else:
            print("🔀 [Lazy Switch] Cache Miss routed! API was executed.")
            return (api_data,)

# ------------------------------------------------------------------------
# NODE 5: Hash Vault Label Builder (v1.4.2)
# ------------------------------------------------------------------------
class HashVaultLabelBuilder:
    """Concatenate up to 4 any-type inputs into one STRING for Hash Vault Save's label.

    Wire style/variant/intensity (or prompt/model/params) once per workflow and never
    manually type a label again. Empty or None inputs are skipped so the output stays
    clean when only some slots are wired.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "separator": ("STRING", {
                    "default": " / ",
                    "multiline": False,
                    "tooltip": "Separator placed between each non-empty input. Defaults to ' / '."
                }),
            },
            "optional": {
                "in_1": (any_type, {"tooltip": "First input. Any type — stringified at join time. None or empty strings are skipped."}),
                "in_2": (any_type, {"tooltip": "Second input."}),
                "in_3": (any_type, {"tooltip": "Third input."}),
                "in_4": (any_type, {"tooltip": "Fourth input."}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("label",)
    FUNCTION = "build"
    CATEGORY = "API Optimization"

    def build(self, separator, in_1=None, in_2=None, in_3=None, in_4=None):
        parts = []
        for value in (in_1, in_2, in_3, in_4):
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            parts.append(text)
        return (separator.join(parts),)


# ------------------------------------------------------------------------
# MAPPINGS: Registering the nodes with ComfyUI
# ------------------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "APICostTracker": APICostTracker,
    "DeterministicHashVault": DeterministicHashVault,
    "HashVaultSave": HashVaultSave,
    "LazyAPISwitch": LazyAPISwitch,
    "HashVaultLabelBuilder": HashVaultLabelBuilder,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "APICostTracker": "💰 API Cost & Quota Tracker",
    "DeterministicHashVault": "🔍 Hash Vault (Check Cache)",
    "HashVaultSave": "💾 Hash Vault (Save API Result)",
    "LazyAPISwitch": "🔀 Lazy API Switch (Bypass)",
    "HashVaultLabelBuilder": "🏷️ Hash Vault Label Builder",
}
