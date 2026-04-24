"""Hash Vault Browser backend route.

Registers GET /akurate/hash_vault/list which walks output/hash_vault/, reads
every {hash}.json sidecar, and returns a JSON array sorted by last_accessed_at
desc. Thumbnails are served by ComfyUI's built-in /view route using
type=output&subfolder=hash_vault&filename={hash}.thumb.png — no extra route
needed on our side.

The .pt files themselves are never read here; they are opaque + expensive to
load, and the sidecar already carries everything the browser UI needs.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    import folder_paths
except ImportError:
    folder_paths = None

try:
    from server import PromptServer
    from aiohttp import web
except ImportError:
    PromptServer = None
    web = None


def _vault_dir() -> str | None:
    if folder_paths is None:
        return None
    return os.path.join(folder_paths.get_output_directory(), "hash_vault")


def _list_entries() -> list[dict[str, Any]]:
    """Walk hash_vault/, read sidecars, return enriched entries sorted by last_accessed_at desc."""
    vault = _vault_dir()
    if not vault or not os.path.isdir(vault):
        return []

    entries: list[dict[str, Any]] = []
    for name in os.listdir(vault):
        if not name.endswith(".json"):
            continue
        sidecar_path = os.path.join(vault, name)
        hash_key = name[:-5]  # strip .json

        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                sidecar = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        pt_path = os.path.join(vault, f"{hash_key}.pt")
        pt_size = os.path.getsize(pt_path) if os.path.exists(pt_path) else 0

        sidecar["pt_size"] = pt_size
        sidecar["pt_exists"] = pt_size > 0
        entries.append(sidecar)

    entries.sort(key=lambda e: e.get("last_accessed_at", ""), reverse=True)
    return entries


if PromptServer is not None and web is not None:
    routes = PromptServer.instance.routes

    @routes.get("/akurate/hash_vault/list")
    async def hash_vault_list(request):
        try:
            data = _list_entries()
            return web.json_response({"entries": data, "count": len(data)})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
