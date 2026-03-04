"""Normalized data models for provider snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


@dataclass
class QuotaItem:
    """Single quota item shown in the UI."""

    id: str
    label: str
    unit: str = "percent"
    remaining_value: Optional[float] = None
    remaining_fraction: Optional[float] = None
    limit_value: Optional[float] = None
    reset_at: Optional[datetime] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize quota item to a JSON-friendly dict."""
        return {
            "id": self.id,
            "label": self.label,
            "unit": self.unit,
            "remaining_value": self.remaining_value,
            "remaining_fraction": self.remaining_fraction,
            "limit_value": self.limit_value,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "meta": self.meta,
        }


@dataclass
class ProviderSnapshot:
    """Normalized snapshot for a provider."""

    id: str
    name: str
    plan: Optional[str] = None
    items: list[QuotaItem] = field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None
    last_update: datetime = field(default_factory=utc_now)
    next_update: Optional[datetime] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize provider snapshot to a JSON-friendly dict."""
        return {
            "id": self.id,
            "name": self.name,
            "plan": self.plan,
            "ok": self.ok,
            "error": self.error,
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "next_update": self.next_update.isoformat() if self.next_update else None,
            "items": [item.to_dict() for item in self.items],
            "meta": self.meta,
        }

