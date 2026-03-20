"""Antigravity provider adapter."""

from __future__ import annotations

from models import ProviderSnapshot
from providers.language_server_provider import LanguageServerProvider


class AntigravityProvider(LanguageServerProvider):
    """Collect quota data from Antigravity language server."""

    provider_id = "antigravity"
    display_name = "Antigravity"
    default_plan = "Pro"
    process_markers = ("antigravity",)

    def collect(self) -> ProviderSnapshot:
        return self.collect_from_language_server()
