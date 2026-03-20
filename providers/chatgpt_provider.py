"""ChatGPT (Codex) provider adapter."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

from models import ProviderSnapshot, QuotaItem
from providers.base_provider import BaseProvider
from utils import try_parse_time

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REFRESH_URL = "https://auth.openai.com/oauth/token"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
REFRESH_AGE_SECONDS = 8 * 24 * 3600  # 8 days


class ChatGPTProvider(BaseProvider):
    """Collect quota data from ChatGPT/Codex usage endpoint."""

    provider_id = "chatgpt"
    display_name = "Codex"
    default_plan = "Pro"

    usage_endpoint = USAGE_URL

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = requests.Session()

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _resolve_auth_paths(self) -> list[Path]:
        """Resolve auth.json file paths in priority order."""
        codex_home = os.environ.get("CODEX_HOME")
        if codex_home:
            return [Path(codex_home) / "auth.json"]
        return [
            Path.home() / ".config" / "codex" / "auth.json",
            Path.home() / ".codex" / "auth.json",
        ]

    def _load_auth(self) -> Optional[tuple[Dict[str, Any], Path]]:
        """Load auth data and return (auth_dict, file_path)."""
        for path in self._resolve_auth_paths():
            if not path.exists():
                self._log(f"auth file not found: {path}")
                continue
            auth = self._read_json(path)
            if not auth:
                continue
            # Check for tokens-based auth (Codex CLI format)
            tokens = auth.get("tokens")
            if isinstance(tokens, dict) and tokens.get("access_token"):
                self._log(f"auth loaded from: {path}")
                return auth, path
            self._log(f"auth file exists but no valid tokens: {path}")
        return None

    def _needs_refresh(self, auth: Dict[str, Any]) -> bool:
        """Check if access token needs refresh."""
        last_refresh = auth.get("last_refresh")
        if not last_refresh:
            return True
        dt = try_parse_time(last_refresh)
        if not dt:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age > REFRESH_AGE_SECONDS

    def _refresh_token(self, auth: Dict[str, Any], auth_path: Path) -> Optional[str]:
        """Refresh the access token using the refresh token."""
        tokens = auth.get("tokens", {})
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            self._log("no refresh_token available")
            return None

        self._log("attempting token refresh")
        try:
            body = urlencode({
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            })
            resp = self._session.post(
                REFRESH_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=body,
                timeout=15.0,
            )

            if resp.status_code in (400, 401):
                raise RuntimeError("Token expired. Run `codex` to log in again.")

            if not (200 <= resp.status_code < 300):
                self._log(f"refresh returned {resp.status_code}")
                return None

            data = resp.json()
            new_access_token = data.get("access_token")
            if not new_access_token:
                self._log("refresh response missing access_token")
                return None

            # Update auth and persist
            tokens["access_token"] = new_access_token
            if data.get("refresh_token"):
                tokens["refresh_token"] = data["refresh_token"]
            if data.get("id_token"):
                tokens["id_token"] = data["id_token"]
            auth["last_refresh"] = datetime.now(timezone.utc).isoformat()

            try:
                auth_path.write_text(json.dumps(auth, indent=2), encoding="utf-8")
                self._log("auth persisted after refresh")
            except Exception as exc:
                self._log(f"failed to persist auth: {exc}")

            return new_access_token
        except RuntimeError:
            raise
        except Exception as exc:
            self._log(f"refresh failed: {exc}")
            return None

    def _fetch_usage(self, access_token: str, account_id: Optional[str] = None) -> requests.Response:
        """Fetch usage data from ChatGPT endpoint."""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "AgentUsageMonitor",
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        return self._session.get(self.usage_endpoint, headers=headers, timeout=self.timeout)

    def _parse_usage(self, resp: requests.Response) -> list[QuotaItem]:
        """Parse usage response into QuotaItems."""
        items: list[QuotaItem] = []
        data = resp.json()
        now_sec = int(datetime.now(timezone.utc).timestamp())

        rate_limit = data.get("rate_limit") or {}
        primary = rate_limit.get("primary_window")
        secondary = rate_limit.get("secondary_window")

        # Try response headers first, then body
        header_primary = self._read_percent(resp.headers.get("x-codex-primary-used-percent"))
        header_secondary = self._read_percent(resp.headers.get("x-codex-secondary-used-percent"))

        primary_used = header_primary
        if primary_used is None and primary and isinstance(primary.get("used_percent"), (int, float)):
            primary_used = primary["used_percent"]

        secondary_used = header_secondary
        if secondary_used is None and secondary and isinstance(secondary.get("used_percent"), (int, float)):
            secondary_used = secondary["used_percent"]

        if primary_used is not None:
            remaining = max(0.0, 100.0 - primary_used)
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:session",
                    label="Session",
                    unit="percent",
                    remaining_value=remaining,
                    remaining_fraction=remaining / 100.0,
                    limit_value=100.0,
                    reset_at=self._get_reset_at(now_sec, primary),
                )
            )

        if secondary_used is not None:
            remaining = max(0.0, 100.0 - secondary_used)
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:weekly",
                    label="Weekly",
                    unit="percent",
                    remaining_value=remaining,
                    remaining_fraction=remaining / 100.0,
                    limit_value=100.0,
                    reset_at=self._get_reset_at(now_sec, secondary),
                )
            )

        # Additional per-model rate limits
        for entry in (data.get("additional_rate_limits") or []):
            if not isinstance(entry, dict) or not entry.get("rate_limit"):
                continue
            name = entry.get("limit_name", "")
            short_name = name.replace("GPT-", "").replace("-Codex-", " ") if name else "Model"
            rl = entry["rate_limit"]
            if rl.get("primary_window") and isinstance(rl["primary_window"].get("used_percent"), (int, float)):
                used = rl["primary_window"]["used_percent"]
                remaining = max(0.0, 100.0 - used)
                items.append(
                    QuotaItem(
                        id=f"{self.provider_id}:model:{short_name}",
                        label=short_name,
                        unit="percent",
                        remaining_value=remaining,
                        remaining_fraction=remaining / 100.0,
                        limit_value=100.0,
                        reset_at=self._get_reset_at(now_sec, rl["primary_window"]),
                    )
                )

        # Code review rate limit
        review_rl = data.get("code_review_rate_limit", {})
        review_window = review_rl.get("primary_window") if review_rl else None
        if review_window and isinstance(review_window.get("used_percent"), (int, float)):
            used = review_window["used_percent"]
            remaining = max(0.0, 100.0 - used)
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:reviews",
                    label="Reviews",
                    unit="percent",
                    remaining_value=remaining,
                    remaining_fraction=remaining / 100.0,
                    limit_value=100.0,
                    reset_at=self._get_reset_at(now_sec, review_window),
                )
            )

        # Credits
        credits_header = self._read_number(resp.headers.get("x-codex-credits-balance"))
        credits_data = None
        if isinstance(data.get("credits"), dict):
            credits_data = self._read_number(data["credits"].get("balance"))
        credits_remaining = credits_header if credits_header is not None else credits_data

        if credits_remaining is not None:
            limit = 1000.0
            used = max(0.0, min(limit, limit - credits_remaining))
            remaining = max(0.0, limit - used)
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:credits",
                    label="Credits",
                    unit="credits",
                    remaining_value=remaining,
                    remaining_fraction=remaining / limit if limit > 0 else 0.0,
                    limit_value=limit,
                    reset_at=None,
                )
            )

        return items

    @staticmethod
    def _read_percent(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            n = float(value)
            return n if 0 <= n <= 100 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _read_number(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _get_reset_at(now_sec: int, window: Optional[Dict[str, Any]]) -> Optional[datetime]:
        """Get reset datetime from a rate limit window."""
        if not window:
            return None
        reset_at = window.get("reset_at")
        if isinstance(reset_at, (int, float)):
            return datetime.fromtimestamp(int(reset_at), tz=timezone.utc)
        reset_after = window.get("reset_after_seconds")
        if isinstance(reset_after, (int, float)):
            return datetime.fromtimestamp(now_sec + int(reset_after), tz=timezone.utc)
        return None

    @staticmethod
    def _detect_plan(data: Dict[str, Any]) -> Optional[str]:
        """Detect plan type from response."""
        plan_type = data.get("plan_type")
        if not plan_type:
            return None
        labels = {
            "free": "Free",
            "plus": "Plus",
            "pro": "Pro",
            "team": "Team",
            "enterprise": "Enterprise",
        }
        return labels.get(str(plan_type).lower(), str(plan_type).capitalize())

    def collect(self) -> ProviderSnapshot:
        auth_result = self._load_auth()
        if not auth_result:
            return self.failure_snapshot("Not logged in. Run `codex` to authenticate.")
        auth, auth_path = auth_result
        tokens = auth.get("tokens", {})
        access_token = tokens.get("access_token")
        account_id = tokens.get("account_id")

        # Proactive token refresh if stale
        if self._needs_refresh(auth):
            self._log("token stale, refreshing proactively")
            refreshed = self._refresh_token(auth, auth_path)
            if refreshed:
                access_token = refreshed

        # Fetch usage
        try:
            resp = self._fetch_usage(access_token, account_id)
        except Exception as exc:
            return self.failure_snapshot(f"Usage request failed: {exc}")

        # Retry with refresh on auth error
        if resp.status_code in (401, 403):
            self._log("got 401/403, attempting token refresh")
            try:
                refreshed = self._refresh_token(auth, auth_path)
            except RuntimeError as exc:
                return self.failure_snapshot(str(exc))
            if refreshed:
                try:
                    resp = self._fetch_usage(refreshed, account_id)
                except Exception as exc:
                    return self.failure_snapshot(f"Usage request failed after refresh: {exc}")
            if resp.status_code in (401, 403):
                return self.failure_snapshot("Token expired. Run `codex` to log in again.")

        if not (200 <= resp.status_code < 300):
            return self.failure_snapshot(f"Usage request failed (HTTP {resp.status_code})")

        try:
            items = self._parse_usage(resp)
        except Exception as exc:
            return self.failure_snapshot(f"Failed to parse usage: {exc}")

        if not items:
            return self.failure_snapshot("No usage data found")

        plan = self._detect_plan(resp.json())
        return self.success_snapshot(items=items, plan=plan, meta={"endpoint": self.usage_endpoint})
