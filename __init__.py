from .api_optimizer_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from . import hash_vault_browser  # registers the /akurate/hash_vault/list route

WEB_DIRECTORY = "./web"

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']

print("\033[34m[ComfyUI-API-Optimizer] \033[92mLoaded Cost Tracker, Hash Vault, and Browser\033[0m")