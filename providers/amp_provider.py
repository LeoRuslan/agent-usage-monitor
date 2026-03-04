"""Amp provider adapter."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import requests

from models import ProviderSnapshot, QuotaItem
from providers.base_provider import BaseProvider

SECRETS_FILE = os.path.expanduser("~/.local/share/amp/secrets.json")
SECRETS_KEY = "apiKey@https://ampcode.com/"
API_URL = "https://ampcode.com/api/internal"


class AmpProvider(BaseProvider):
    """Collect quota data from Amp via JSON-RPC API."""

    provider_id = "amp"
    display_name = "Amp"
    default_plan = "Free"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = requests.Session()

    def _load_api_key(self) -> Optional[str]:
        """Read API key from Amp CLI secrets file."""
        if not os.path.exists(SECRETS_FILE):
            self._log(f"secrets file not found: {SECRETS_FILE}")
            return None
        try:
            with open(SECRETS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = data.get(SECRETS_KEY)
            if key:
                self._log("API key loaded from secrets file")
            return key
        except Exception as exc:
            self._log(f"secrets file read failed: {exc}")
            return None

    def _fetch_balance(self, api_key: str) -> Dict[str, Any]:
        """Call the Amp JSON-RPC API for balance info."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {"method": "userDisplayBalanceInfo", "params": {}}
        resp = self._session.post(API_URL, headers=headers, json=body, timeout=self.timeout)

        if resp.status_code in (401, 403):
            raise RuntimeError("Session expired. Re-authenticate in Amp Code.")
        if not (200 <= resp.status_code < 300):
            detail = ""
            try:
                err = resp.json()
                detail = err.get("error", {}).get("message", "")
            except Exception:
                pass
            if detail:
                raise RuntimeError(detail)
            raise RuntimeError(f"Request failed (HTTP {resp.status_code})")

        return resp.json()

    @staticmethod
    def _parse_money(s: str) -> Optional[float]:
        """Parse dollar string like '1,234.56' to float."""
        try:
            return float(s.replace(",", ""))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_display_text(text: str) -> Dict[str, Any]:
        """Parse the displayText string from Amp API response."""
        result: Dict[str, Any] = {
            "remaining": None,
            "total": None,
            "hourly_rate": 0.0,
            "bonus_pct": None,
            "bonus_days": None,
            "credits": None,
        }

        # $remaining/$total remaining
        m = re.search(
            r"\$([0-9][0-9,]*(?:\.[0-9]+)?)/\$([0-9][0-9,]*(?:\.[0-9]+)?)\s+remaining",
            text,
        )
        if m:
            remaining = AmpProvider._parse_money(m.group(1))
            total = AmpProvider._parse_money(m.group(2))
            if remaining is not None and total is not None:
                result["remaining"] = remaining
                result["total"] = total

        # replenishes +$rate/hour
        m = re.search(r"replenishes \+\$([0-9][0-9,]*(?:\.[0-9]+)?)/hour", text)
        if m:
            rate = AmpProvider._parse_money(m.group(1))
            if rate is not None:
                result["hourly_rate"] = rate

        # +N% bonus for N more days
        m = re.search(r"\+(\d+)% bonus for (\d+) more days?", text)
        if m:
            result["bonus_pct"] = int(m.group(1))
            result["bonus_days"] = int(m.group(2))

        # Individual credits: $N remaining
        m = re.search(r"Individual credits: \$([0-9][0-9,]*(?:\.[0-9]+)?)\s+remaining", text)
        if m:
            result["credits"] = AmpProvider._parse_money(m.group(1))

        return result

    def collect(self) -> ProviderSnapshot:
        api_key = self._load_api_key()
        if not api_key:
            return self.failure_snapshot(
                "Amp not installed. Install Amp Code to get started."
            )

        try:
            resp_json = self._fetch_balance(api_key)
        except RuntimeError as exc:
            return self.failure_snapshot(str(exc))
        except Exception as exc:
            return self.failure_snapshot(f"Request failed: {exc}")

        if not resp_json.get("ok") or not resp_json.get("result", {}).get("displayText"):
            return self.failure_snapshot("Could not parse usage data.")

        display_text = resp_json["result"]["displayText"]
        balance = self._parse_display_text(display_text)

        if balance["total"] is None and balance["credits"] is None:
            return self.failure_snapshot("Could not parse usage data.")

        items: list[QuotaItem] = []
        plan = "Free"

        # Free tier progress
        if balance["total"] is not None:
            total = balance["total"]
            remaining = balance["remaining"] or 0.0
            used = max(0.0, total - remaining)
            fraction = remaining / total if total > 0 else 0.0

            # Estimate reset time based on hourly replenishment rate
            reset_at = None
            if used > 0 and balance["hourly_rate"] > 0:
                hours_to_full = used / balance["hourly_rate"]
                reset_at = datetime.now(timezone.utc) + timedelta(hours=hours_to_full)

            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:free",
                    label="Free",
                    unit="usd",
                    remaining_value=remaining,
                    remaining_fraction=fraction,
                    limit_value=total,
                    reset_at=reset_at,
                    meta={"hourly_rate": balance["hourly_rate"]},
                )
            )

        # Credits
        if balance["credits"] is not None and balance["total"] is None:
            plan = "Credits"

        if balance["credits"] is not None and (balance["credits"] > 0 or balance["total"] is None):
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:credits",
                    label="Credits",
                    unit="usd",
                    remaining_value=balance["credits"],
                    remaining_fraction=None,
                    limit_value=None,
                    reset_at=None,
                )
            )

        if not items:
            return self.failure_snapshot("No usage data found in Amp response.")

        return self.success_snapshot(items=items, plan=plan, meta={"source": "api"})
