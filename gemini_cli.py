"""Gemini API/CLI probe for quota monitoring."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional

import requests

from config import (
    GEMINI_QUOTA_ENDPOINT,
    DEFAULT_GEMINI_CREDS,
    DEFAULT_GEMINI_SETTINGS,
    DEFAULT_TIMEOUT,
)
from utils import try_parse_time


class GeminiProbe:
    """Probes Gemini API or CLI for quota information."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, gemini_cli: Optional[str] = None, verbose: bool = False):
        self.timeout = timeout
        self.gemini_cli = gemini_cli or os.environ.get("GEMINI_CLI_PATH", "gemini")
        self.session = requests.Session()
        self.verbose = verbose

    @staticmethod
    def _read_json_file(path: str) -> Optional[Dict[str, Any]]:
        """Read and parse a JSON file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def try_api_quota(self, access_token: str) -> Optional[Dict[str, Any]]:
        """Try to get quota via API endpoint."""
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        try:
            if self.verbose:
                print("[gemini] calling quota API endpoint")
            r = self.session.post(GEMINI_QUOTA_ENDPOINT, headers=headers, json={}, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if self.verbose:
                print(f"[gemini] api call failed: {e}")
            return None

    def try_cli_stats(self) -> Optional[Dict[str, Any]]:
        """Try to get stats via CLI."""
        attempts = [
            [self.gemini_cli, "stats", "--json"],
            [self.gemini_cli, "/stats", "--json"],
            [self.gemini_cli, "stats"],
            [self.gemini_cli, "/stats"],
        ]
        for cmd in attempts:
            if self.verbose:
                print(f"[gemini] trying CLI: {' '.join(cmd)}")
            try:
                out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=self.timeout)
                out = out.strip()
                if self.verbose:
                    print(f"[gemini] CLI output (first 500 chars):\n{out[:500]}")
                try:
                    parsed = json.loads(out)
                    return {"source": "cli-json", "payload": parsed}
                except Exception:
                    return {"source": "cli-raw", "payload": out}
            except FileNotFoundError:
                if self.verbose:
                    print(f"[gemini] binary not found at {self.gemini_cli}")
                return None
            except subprocess.CalledProcessError as e:
                if self.verbose:
                    print(f"[gemini] CLI returned non-zero: {e.returncode}; output (first 300 chars):\n{e.output[:300]}")
                continue
            except Exception as e:
                if self.verbose:
                    print(f"[gemini] CLI attempt failed: {e}")
                continue
        return None

    @staticmethod
    def _extract_quota_from_api_resp(resp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Extract quota info from API response."""
        def find_keys(d: Any, keys: List[str]) -> List[Any]:
            found = []
            if isinstance(d, dict):
                for k, v in d.items():
                    if k in keys:
                        found.append(v)
                    found.extend(find_keys(v, keys))
            elif isinstance(d, list):
                for el in d:
                    found.extend(find_keys(el, keys))
            return found

        rem_candidates = find_keys(resp, ["remainingFraction", "remaining_fraction", "remaining", "remainingPercent"])
        reset_candidates = find_keys(resp, ["resetTime", "reset_time", "resetAt", "resetAtMs", "reset"])

        rem = None
        for c in rem_candidates:
            try:
                if isinstance(c, str) and "%" in c:
                    c = c.replace("%", "").strip()
                val = float(c)
                if 0.0 <= val <= 1.0:
                    rem = val
                    break
                if 1.0 < val <= 100.0:
                    rem = val / 100.0
                    break
            except Exception:
                continue

        reset_dt = None
        for r in reset_candidates:
            reset_dt = try_parse_time(r)
            if reset_dt:
                break

        if rem is None and reset_dt is None:
            return None
        return {"remaining_fraction": rem, "reset_time": (reset_dt.isoformat() if reset_dt else None), "raw": resp}

    @staticmethod
    def _extract_quota_from_cli_raw(payload: Any) -> Optional[Dict[str, Any]]:
        """Extract quota info from raw CLI output."""
        if isinstance(payload, dict):
            return GeminiProbe._extract_quota_from_api_resp(payload)

        txt = str(payload)
        m = re.search(r"Remaining[:\s]+([0-9]+(?:\.[0-9]+)?)[\s%]*", txt, re.IGNORECASE)
        rem = None
        if m:
            try:
                val = float(m.group(1))
                rem = val / 100.0 if val > 1 else val
            except Exception:
                rem = None

        m2 = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)", txt)
        reset_dt = try_parse_time(m2.group(1)) if m2 else None

        if rem is None and reset_dt is None:
            return None
        return {"remaining_fraction": rem, "reset_time": (reset_dt.isoformat() if reset_dt else None), "raw_text": txt}

    def run(self) -> Dict[str, Any]:
        """Execute the probe and return results."""
        creds_path = os.environ.get("GEMINI_CREDS_PATH", DEFAULT_GEMINI_CREDS)
        settings_path = os.environ.get("GEMINI_SETTINGS_PATH", DEFAULT_GEMINI_SETTINGS)

        if self.verbose:
            print(f"[gemini] gemini_cli={self.gemini_cli}")
            print(f"[gemini] creds_path={creds_path} exists={os.path.exists(creds_path)}")
            print(f"[gemini] settings_path={settings_path} exists={os.path.exists(settings_path)}")

        creds = self._read_json_file(creds_path)
        if creds and isinstance(creds, dict):
            token = creds.get("access_token") or creds.get("token")
            if token:
                if self.verbose:
                    print("[gemini] found access token in creds, trying API")
                api_resp = self.try_api_quota(token)
                if api_resp:
                    parsed = self._extract_quota_from_api_resp(api_resp)
                    if parsed:
                        return {"ok": True, "method": "api", "parsed": parsed}
                    return {"ok": True, "method": "api", "parsed": None, "raw": api_resp}

        if self.verbose:
            print("[gemini] trying CLI fallback")
        cli_resp = self.try_cli_stats()
        if cli_resp:
            if cli_resp.get("source") == "cli-json":
                parsed = self._extract_quota_from_api_resp(cli_resp.get("payload"))
                if parsed:
                    return {"ok": True, "method": "cli-json", "parsed": parsed}
                return {"ok": True, "method": "cli-json", "parsed": None, "raw": cli_resp.get("payload")}
            elif cli_resp.get("source") == "cli-raw":
                parsed = self._extract_quota_from_cli_raw(cli_resp.get("payload"))
                if parsed:
                    return {"ok": True, "method": "cli-raw", "parsed": parsed}
                return {"ok": True, "method": "cli-raw", "parsed": None, "raw_text": cli_resp.get("payload")}

        return {"ok": False, "reason": "No Gemini credentials or CLI output found / parsed"}
