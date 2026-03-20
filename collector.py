"""Provider collection orchestration."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable

from models import ProviderSnapshot
from providers.registry import create_provider, list_provider_ids, resolve_provider_id


class ProviderCollector:
    """Collect snapshots from one or many providers."""

    def __init__(self, timeout: float = 8.0, verbose: bool = False, refresh_interval_seconds: int = 240):
        self.timeout = timeout
        self.verbose = verbose
        self.refresh_interval_seconds = refresh_interval_seconds

    def collect(self, provider_ids: Iterable[str] | None = None) -> Dict[str, ProviderSnapshot]:
        """Collect snapshots for selected providers."""
        canonical_ids = self._resolve_ids(provider_ids)
        if not canonical_ids:
            return {}

        results: Dict[str, ProviderSnapshot] = {}
        with ThreadPoolExecutor(max_workers=len(canonical_ids)) as pool:
            futures = {pool.submit(self._collect_one, provider_id): provider_id for provider_id in canonical_ids}
            for future in as_completed(futures):
                provider_id = futures[future]
                results[provider_id] = future.result()

        # Preserve requested ordering in returned dictionary
        ordered = {provider_id: results[provider_id] for provider_id in canonical_ids}
        return ordered

    def _collect_one(self, provider_id: str) -> ProviderSnapshot:
        provider = create_provider(
            provider_id,
            timeout=self.timeout,
            verbose=self.verbose,
            refresh_interval_seconds=self.refresh_interval_seconds,
        )
        snapshot = provider.safe_collect()
        if not snapshot.ok:
            time.sleep(1)
            snapshot = provider.safe_collect()
        return snapshot

    @staticmethod
    def _resolve_ids(provider_ids: Iterable[str] | None) -> list[str]:
        if provider_ids is None:
            return list_provider_ids()

        out: list[str] = []
        for provider_id in provider_ids:
            resolved = resolve_provider_id(provider_id)
            if resolved not in out:
                out.append(resolved)
        return out

