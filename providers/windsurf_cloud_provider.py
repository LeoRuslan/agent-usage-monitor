"""Simplified Windsurf provider using only cloud API."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, Optional

from models import ProviderSnapshot, QuotaItem
from providers.base_provider import BaseProvider
from utils import try_parse_time

CLOUD_URL = "https://server.codeium.com"
CLOUD_SERVICE = "exa.seat_management_pb.SeatManagementService"

VARIANTS = [
    {
        "marker": "windsurf",
        "ide_name": "windsurf",
        "state_db": os.path.expanduser(
            "~/Library/Application Support/Windsurf/User/globalStorage/state.vscdb"
        ),
    },
    {
        "marker": "windsurf-next",
        "ide_name": "windsurf-next",
        "state_db": os.path.expanduser(
            "~/Library/Application Support/Windsurf - Next/User/globalStorage/state.vscdb"
        ),
    },
]


class WindsurfCloudProvider(BaseProvider):
    """Simplified Windsurf provider using only cloud API."""

    provider_id = "windsurf-cloud"
    display_name = "Windsurf (Cloud)"
    default_plan = "Free"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = self._create_session()

    def _create_session(self):
        import requests
        session = requests.Session()
        return session

    def _read_api_key(self, db_path: str) -> Optional[str]:
        """Read API key from Windsurf's SQLite state DB."""
        if not os.path.exists(db_path):
            self._log(f"state db not found: {db_path}")
            return None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT value FROM ItemTable WHERE key = 'windsurfAuthStatus' LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()
            if not row or not row[0]:
                return None
            auth = json.loads(row[0])
            api_key = auth.get("apiKey")
            if api_key:
                self._log("API key loaded from SQLite")
            return api_key
        except Exception as exc:
            self._log(f"SQLite read failed: {exc}")
            return None

    def _parse_credit_items(self, user_status: Dict[str, Any]) -> tuple[list[QuotaItem], Optional[str]]:
        """Parse credit-based planStatus into QuotaItems."""
        plan_status = user_status.get("planStatus") or {}
        plan_info = plan_status.get("planInfo") or {}
        plan_name = plan_info.get("planName")

        plan_end = plan_status.get("planEnd")
        reset_at = try_parse_time(plan_end)

        items: list[QuotaItem] = []

        # Prompt credits
        prompt_total = plan_status.get("availablePromptCredits")
        prompt_used = plan_status.get("usedPromptCredits", 0)
        self._log(f"Prompt credits - total: {prompt_total}, used: {prompt_used}")
        
        if isinstance(prompt_total, (int, float)):
            total = prompt_total / 100.0
            used = (prompt_used or 0) / 100.0
            remaining = max(0.0, total - used)
            fraction = remaining / total if total > 0 else 0.0
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:prompt_credits",
                    label="Prompt credits",
                    unit="credits",
                    remaining_value=remaining,
                    remaining_fraction=fraction,
                    limit_value=total,
                    reset_at=reset_at,
                )
            )

        # Flex credits
        flex_total = plan_status.get("availableFlexCredits")
        flex_used = plan_status.get("usedFlexCredits", 0)
        self._log(f"Flex credits - total: {flex_total}, used: {flex_used}")
        
        if isinstance(flex_total, (int, float)):
            total = flex_total / 100.0
            used = (flex_used or 0) / 100.0
            remaining = max(0.0, total - used)
            fraction = remaining / total if total > 0 else 0.0
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:flex_credits",
                    label="Flex credits",
                    unit="credits",
                    remaining_value=remaining,
                    remaining_fraction=fraction,
                    limit_value=total,
                    reset_at=None,
                )
            )

        return items, plan_name

    def _call_cloud_api(self, api_key: str, ide_name: str, version: str = "1.9566.11") -> Optional[Dict[str, Any]]:
        """Call Windsurf cloud API."""
        url = f"{CLOUD_URL}/{CLOUD_SERVICE}/GetUserStatus"
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        }
        payload = {
            "metadata": {
                "apiKey": api_key,
                "ideName": ide_name,
                "ideVersion": version,
                "extensionName": ide_name,
                "extensionVersion": version,
                "locale": "en",
            }
        }
        
        self._log(f"Calling cloud API for {ide_name} with version {version}")
        
        try:
            resp = self._session.post(url, headers=headers, json=payload, timeout=15.0)
            if 200 <= resp.status_code < 300:
                data = resp.json()
                self._log(f"Cloud API response received for {ide_name}")
                return data
            else:
                self._log(f"Cloud API failed with status {resp.status_code} for {ide_name}")
        except Exception as exc:
            self._log(f"Cloud API request failed for {ide_name}: {exc}")
        return None

    def collect(self) -> ProviderSnapshot:
        """Collect data using only cloud API."""
        for variant in VARIANTS:
            api_key = self._read_api_key(variant["state_db"])
            if not api_key:
                continue

            data = self._call_cloud_api(api_key, variant["ide_name"])
            if not data:
                continue

            user_status = data.get("userStatus")
            if not user_status:
                continue

            items, plan_name = self._parse_credit_items(user_status)
            if not items:
                continue

            return self.success_snapshot(
                items=items,
                plan=plan_name,
                meta={"source": "cloud_only", "variant": variant["marker"]},
            )

        return self.failure_snapshot("Start Windsurf and sign in, or check API key")
