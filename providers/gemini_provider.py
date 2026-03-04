"""Gemini provider adapter."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

import requests

from models import ProviderSnapshot, QuotaItem
from providers.base_provider import BaseProvider
from utils import try_parse_time

QUOTA_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
DEFAULT_CREDS_PATH = os.path.expanduser("~/.gemini/oauth_creds.json")


class GeminiProvider(BaseProvider):
    """Collect quota data from Gemini API or CLI."""

    provider_id = "gemini"
    display_name = "Gemini"
    default_plan = "Pro"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = requests.Session()
        self._cli = os.environ.get("GEMINI_CLI_PATH", "gemini")

    @staticmethod
    def _read_json_file(path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _discover_project_id(self, access_token: str) -> Optional[str]:
        endpoint = "https://cloudresourcemanager.googleapis.com/v1/projects"
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = self._session.get(endpoint, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            for project in resp.json().get("projects", []):
                pid = project.get("projectId", "")
                if pid.startswith("gen-lang-client"):
                    return pid
        except Exception:
            pass
        return None

    def _try_api(self, access_token: str) -> Optional[Dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        project_id = self._discover_project_id(access_token)
        payload = {"project": project_id} if project_id else {}
        try:
            resp = self._session.post(
                QUOTA_ENDPOINT, headers=headers, json=payload, timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _try_cli(self) -> Optional[Dict[str, Any]]:
        attempts = [
            [self._cli, "stats", "--json"],
            [self._cli, "/stats", "--json"],
            [self._cli, "stats"],
            [self._cli, "/stats"],
        ]
        for cmd in attempts:
            try:
                out = subprocess.check_output(
                    cmd, stderr=subprocess.STDOUT, text=True, timeout=self.timeout,
                ).strip()
                try:
                    return {"source": "cli-json", "payload": json.loads(out)}
                except json.JSONDecodeError:
                    return {"source": "cli-raw", "payload": out}
            except FileNotFoundError:
                return None
            except subprocess.CalledProcessError:
                continue
            except Exception:
                continue
        return None

    def _parse_buckets(self, resp: Dict[str, Any]) -> list[QuotaItem]:
        buckets = resp.get("buckets", [])
        if not buckets:
            return self._parse_legacy(resp)

        items: list[QuotaItem] = []
        for bucket in buckets:
            model_id = bucket.get("modelId")
            raw_fraction = bucket.get("remainingFraction")
            if not model_id or raw_fraction is None:
                continue
            fraction = max(0.0, min(1.0, float(raw_fraction)))
            reset_at = try_parse_time(bucket.get("resetTime"))
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:{model_id}",
                    label=self._format_model_name(model_id),
                    unit="percent",
                    remaining_value=fraction * 100.0,
                    remaining_fraction=fraction,
                    limit_value=100.0,
                    reset_at=reset_at,
                )
            )
        return items

    def _parse_legacy(self, resp: Dict[str, Any]) -> list[QuotaItem]:
        """Fallback parser for non-bucket responses."""
        rem_candidates = self._find_keys(resp, ["remainingFraction", "remaining_fraction", "remaining", "remainingPercent"])
        reset_candidates = self._find_keys(resp, ["resetTime", "reset_time", "resetAt", "resetAtMs", "reset"])

        fraction = None
        for c in rem_candidates:
            try:
                val = float(str(c).replace("%", "").strip())
                if 0.0 <= val <= 1.0:
                    fraction = val
                    break
                if 1.0 < val <= 100.0:
                    fraction = val / 100.0
                    break
            except (TypeError, ValueError):
                continue

        reset_at = None
        for r in reset_candidates:
            reset_at = try_parse_time(r)
            if reset_at:
                break

        if fraction is None and reset_at is None:
            return []

        return [
            QuotaItem(
                id=f"{self.provider_id}:default",
                label="Gemini Quota",
                unit="percent",
                remaining_value=(fraction * 100.0 if fraction is not None else None),
                remaining_fraction=fraction,
                limit_value=100.0,
                reset_at=reset_at,
            )
        ]

    def _parse_cli_raw(self, text: str) -> list[QuotaItem]:
        fraction = None
        m = re.search(r"Remaining[:\s]+([0-9]+(?:\.[0-9]+)?)[\s%]*", text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                fraction = val / 100.0 if val > 1 else val
            except (TypeError, ValueError):
                pass

        reset_at = None
        m2 = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)", text)
        if m2:
            reset_at = try_parse_time(m2.group(1))

        if fraction is None and reset_at is None:
            return []

        return [
            QuotaItem(
                id=f"{self.provider_id}:cli",
                label="Gemini Quota",
                unit="percent",
                remaining_value=(fraction * 100.0 if fraction is not None else None),
                remaining_fraction=fraction,
                limit_value=100.0,
                reset_at=reset_at,
            )
        ]

    @staticmethod
    def _format_model_name(model_id: str) -> str:
        name = model_id.replace("gemini-", "Gemini ")
        name = name.replace("-preview", " (Preview)")
        name = name.replace("-lite", " Lite")
        name = name.replace("-pro", " Pro")
        name = name.replace("-flash", " Flash")
        return " ".join(name.split())

    @staticmethod
    def _find_keys(data: Any, keys: List[str]) -> List[Any]:
        found: List[Any] = []
        if isinstance(data, dict):
            for k, v in data.items():
                if k in keys:
                    found.append(v)
                found.extend(GeminiProvider._find_keys(v, keys))
        elif isinstance(data, list):
            for el in data:
                found.extend(GeminiProvider._find_keys(el, keys))
        return found

    def collect(self) -> ProviderSnapshot:
        creds_path = os.environ.get("GEMINI_CREDS_PATH", DEFAULT_CREDS_PATH)
        creds = self._read_json_file(creds_path)

        if creds and isinstance(creds, dict):
            token = creds.get("access_token") or creds.get("token")
            if token:
                api_resp = self._try_api(token)
                if api_resp:
                    items = self._parse_buckets(api_resp)
                    if items:
                        return self.success_snapshot(items=items, meta={"method": "api"})

        cli_resp = self._try_cli()
        if cli_resp:
            if cli_resp["source"] == "cli-json":
                payload = cli_resp["payload"]
                items = self._parse_buckets(payload) if isinstance(payload, dict) else []
                if items:
                    return self.success_snapshot(items=items, meta={"method": "cli-json"})
            elif cli_resp["source"] == "cli-raw":
                items = self._parse_cli_raw(str(cli_resp["payload"]))
                if items:
                    return self.success_snapshot(items=items, meta={"method": "cli-raw"})

        return self.failure_snapshot("No Gemini credentials or CLI output found")
