"""Provider registry and aliases."""

from __future__ import annotations

from importlib import import_module
from typing import Dict

from providers.base_provider import BaseProvider

PROVIDER_SPECS: Dict[str, str] = {
    "antigravity": "providers.antigravity_provider:AntigravityProvider",
    "amp": "providers.amp_provider:AmpProvider",
    "chatgpt": "providers.chatgpt_provider:ChatGPTProvider",
    "gemini": "providers.gemini_provider:GeminiProvider",
    "windsurf": "providers.windsurf_cloud_provider:WindsurfCloudProvider",
}

PROVIDER_ALIASES = {
    "codex": "chatgpt",
    "gemini_cli": "gemini",
}


def resolve_provider_id(provider_id: str) -> str:
    """Resolve provider aliases to canonical IDs."""
    return PROVIDER_ALIASES.get(provider_id, provider_id)


def list_provider_ids() -> list[str]:
    """Return canonical provider IDs."""
    return sorted(PROVIDER_SPECS.keys())


def list_provider_choices() -> list[str]:
    """Return allowed CLI choice values."""
    return ["all", *list_provider_ids(), *sorted(PROVIDER_ALIASES.keys())]


def create_provider(provider_id: str, **kwargs) -> BaseProvider:
    """Create provider instance by ID or alias."""
    canonical = resolve_provider_id(provider_id)
    spec = PROVIDER_SPECS.get(canonical)
    if spec is None:
        known = ", ".join(list_provider_ids())
        raise ValueError(f"Unknown provider '{provider_id}'. Known providers: {known}")
    module_name, class_name = spec.split(":", 1)
    module = import_module(module_name)
    cls = getattr(module, class_name)
    return cls(**kwargs)
