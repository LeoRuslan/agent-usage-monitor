"""Base contract for all quota providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import timedelta
from typing import Optional

from models import ProviderSnapshot, QuotaItem, utc_now


class BaseProvider(ABC):
    """Abstract provider interface."""

    provider_id = "unknown"
    display_name = "Unknown"
    default_plan: Optional[str] = None

    def __init__(self, timeout: float = 8.0, verbose: bool = False, refresh_interval_seconds: int = 240):
        self.timeout = timeout
        self.verbose = verbose
        self.refresh_interval_seconds = refresh_interval_seconds

    @abstractmethod
    def collect(self) -> ProviderSnapshot:
        """Collect normalized provider snapshot."""
        raise NotImplementedError

    def safe_collect(self) -> ProviderSnapshot:
        """Run collection with exception safety."""
        try:
            snapshot = self.collect()
            if snapshot.next_update is None:
                snapshot.next_update = snapshot.last_update + timedelta(seconds=self.refresh_interval_seconds)
            return snapshot
        except Exception as exc:
            return self.failure_snapshot(str(exc))

    def success_snapshot(
        self,
        items: list[QuotaItem],
        plan: Optional[str] = None,
        meta: Optional[dict] = None,
    ) -> ProviderSnapshot:
        """Create success snapshot."""
        now = utc_now()
        return ProviderSnapshot(
            id=self.provider_id,
            name=self.display_name,
            plan=plan if plan is not None else self.default_plan,
            items=items,
            ok=True,
            error=None,
            last_update=now,
            next_update=now + timedelta(seconds=self.refresh_interval_seconds),
            meta=meta or {},
        )

    def failure_snapshot(self, error: str, meta: Optional[dict] = None) -> ProviderSnapshot:
        """Create failed snapshot."""
        now = utc_now()
        return ProviderSnapshot(
            id=self.provider_id,
            name=self.display_name,
            plan=self.default_plan,
            items=[],
            ok=False,
            error=error,
            last_update=now,
            next_update=now + timedelta(seconds=self.refresh_interval_seconds),
            meta=meta or {},
        )

    def _log(self, message: str) -> None:
        """Verbose logger."""
        if self.verbose:
            print(f"[{self.provider_id}] {message}")

