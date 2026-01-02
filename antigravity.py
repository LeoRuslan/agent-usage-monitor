"""Antigravity IDE probe for quota monitoring."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import psutil
import requests

from config import (
    ANTIGRAVITY_GETUNLEASH_PATH,
    ANTIGRAVITY_GETUSERSTATUS_PATH,
    ANTIGRAVITY_GETCOMMANDMODELCONFIGS_PATH,
    DEFAULT_TIMEOUT,
    VERIFY_SSL,
)
from utils import try_parse_time, pretty_pct

# Silence local TLS warnings (local self-signed)
requests.packages.urllib3.disable_warnings()


@dataclass
class AntigravityQuotaItem:
    """Represents a single quota item for a model."""
    label: str
    remaining_fraction: Optional[float]
    reset_time: Optional[datetime]


class AntigravityProbe:
    """Probes Antigravity language server for quota information."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT, verify_ssl: bool = VERIFY_SSL, verbose: bool = False):
        self.timeout = timeout
        self.session = requests.Session()
        self.verify_ssl = verify_ssl
        self.verbose = verbose

    def find_process(self) -> Optional[psutil.Process]:
        """Find the Antigravity language server process."""
        for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                cmdline = " ".join(p.info.get("cmdline") or [])
                if "language_server" in name or "language_server" in cmdline:
                    if "antigravity" in cmdline.lower() or "--app_data_dir antigravity" in cmdline.lower():
                        return p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    @staticmethod
    def _extract_flag_from_cmd(cmdline: str, flag: str) -> Optional[str]:
        """Extract a flag value from command line string."""
        m = re.search(rf"{re.escape(flag)}(?:=|\s+)([^\s]+)", cmdline)
        return m.group(1) if m else None

    def get_listening_ports(self, pid: int) -> List[int]:
        """Get all listening ports for a given process ID."""
        ports = set()
        try:
            for conn in psutil.net_connections(kind="inet"):
                try:
                    if conn.status == psutil.CONN_LISTEN and conn.pid == pid and conn.laddr:
                        ports.add(conn.laddr.port)
                except Exception:
                    continue
        except Exception:
            pass

        if not ports:
            try:
                out = subprocess.check_output(
                    ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-p", str(pid)],
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                for line in out.splitlines():
                    m = re.search(r":(\d+)\s+\(LISTEN\)", line)
                    if m:
                        ports.add(int(m.group(1)))
            except Exception:
                pass

        return sorted(ports)

    def probe_connect_port(self, ports: List[int], csrf_token: str) -> Optional[int]:
        """Find a working port by probing with test requests."""
        headers = {
            "X-Codeium-Csrf-Token": csrf_token,
            "Connect-Protocol-Version": "1",
            "Content-Type": "application/json",
        }
        path = ANTIGRAVITY_GETUNLEASH_PATH
        for port in ports:
            url = f"https://127.0.0.1:{port}{path}"
            if self.verbose:
                print(f"[antigravity] probing {url}")
            try:
                r = self.session.post(url, headers=headers, json={}, timeout=2.0, verify=self.verify_ssl)
                if r.status_code == 200:
                    if self.verbose:
                        print(f"[antigravity] probe ok on port {port}")
                    return port
            except requests.RequestException:
                continue
        return None

    def fetch_user_status(self, port: int, csrf_token: str) -> Dict[str, Any]:
        """Fetch user status from the language server."""
        headers = {"X-Codeium-Csrf-Token": csrf_token, "Content-Type": "application/json"}
        url_primary = f"https://127.0.0.1:{port}{ANTIGRAVITY_GETUSERSTATUS_PATH}"
        url_fallback = f"https://127.0.0.1:{port}{ANTIGRAVITY_GETCOMMANDMODELCONFIGS_PATH}"
        payload = {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en", "ideVersion": "unknown"}

        try:
            if self.verbose:
                print(f"[antigravity] POST {url_primary}")
            r = self.session.post(url_primary, headers=headers, json=payload, timeout=self.timeout, verify=self.verify_ssl)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass

        try:
            if self.verbose:
                print(f"[antigravity] POST fallback {url_fallback}")
            r = self.session.post(url_fallback, headers=headers, json=payload, timeout=self.timeout, verify=self.verify_ssl)
            if r.status_code == 200:
                return r.json()
            try:
                return r.json()
            except Exception:
                pass
        except requests.RequestException:
            pass

        raise RuntimeError("Antigravity quota endpoints failed")

    def parse_quota_items(self, user_status_json: Dict[str, Any]) -> List[AntigravityQuotaItem]:
        """Parse user status JSON into quota items."""
        items: List[AntigravityQuotaItem] = []
        try:
            configs = (
                user_status_json.get("userStatus", {})
                .get("cascadeModelConfigData", {})
                .get("clientModelConfigs", [])
            )
        except Exception:
            configs = []

        if not isinstance(configs, list):
            configs = []

        for c in configs:
            label = c.get("label") or c.get("modelLabel") or ""
            quota = c.get("quotaInfo", {}) or {}
            rem = quota.get("remainingFraction")
            reset = quota.get("resetTime")
            reset_dt = try_parse_time(reset)
            rem_val = None
            if isinstance(rem, (int, float)):
                rem_val = float(rem)
            else:
                try:
                    rem_val = float(rem)
                except Exception:
                    rem_val = None
            items.append(AntigravityQuotaItem(label=label, remaining_fraction=rem_val, reset_time=reset_dt))
        return items

    def best_mapping_choice(self, items: List[AntigravityQuotaItem]) -> Optional[AntigravityQuotaItem]:
        """Select the best primary model for display."""
        def lower(s: str) -> str:
            return (s or "").lower()

        for it in items:
            if "claude" in lower(it.label) and "thinking" not in lower(it.label):
                return it
        for it in items:
            ll = lower(it.label)
            if "pro" in ll and "low" in ll:
                return it
        for it in items:
            ll = lower(it.label)
            if "gemini" in ll and "flash" in ll:
                return it
        chosen = None
        for it in items:
            if it.remaining_fraction is None:
                continue
            if chosen is None or it.remaining_fraction < chosen.remaining_fraction:
                chosen = it
        return chosen

    def run(self) -> Dict[str, Any]:
        """Execute the probe and return results."""
        p = self.find_process()
        if p is None:
            return {"ok": False, "reason": "Antigravity language server process not found"}

        try:
            cmdline = " ".join(p.cmdline())
        except Exception:
            cmdline = ""
        pid = p.pid

        if self.verbose:
            print(f"[antigravity] pid={pid} cmdline={cmdline}")

        csrf_token = self._extract_flag_from_cmd(cmdline, "--csrf_token")
        ext_port_flag = self._extract_flag_from_cmd(cmdline, "--extension_server_port")
        ports = self.get_listening_ports(pid)

        if self.verbose:
            print(f"[antigravity] listening ports: {ports} ext_port_flag={ext_port_flag}")

        if ext_port_flag:
            try:
                ext_port = int(ext_port_flag)
                ports = [ext_port] + [x for x in ports if x != ext_port]
            except Exception:
                pass

        if not csrf_token:
            return {"ok": False, "reason": "CSRF token not found in process arguments"}

        if not ports:
            return {"ok": False, "reason": f"No listening ports found for pid {pid}"}

        connect_port = self.probe_connect_port(ports, csrf_token)
        if not connect_port:
            return {"ok": False, "reason": "Could not find working connect port (probe failed)"}

        try:
            js = self.fetch_user_status(connect_port, csrf_token)
        except Exception as e:
            return {"ok": False, "reason": f"Failed to fetch user status: {e}"}

        items = self.parse_quota_items(js)
        mapped = self.best_mapping_choice(items)
        parsed_items = [
            {
                "label": it.label,
                "remaining_fraction": it.remaining_fraction,
                "remaining_percent": pretty_pct(it.remaining_fraction),
                "reset_time": (it.reset_time.isoformat() if it.reset_time else None),
            }
            for it in items
        ]
        out = {
            "ok": True,
            "pid": pid,
            "cmdline": cmdline,
            "connect_port": connect_port,
            "items": parsed_items,
            "mapped_primary": {
                "label": mapped.label if mapped else None,
                "remaining_fraction": mapped.remaining_fraction if mapped else None,
                "remaining_percent": pretty_pct(mapped.remaining_fraction if mapped else None),
                "reset_time": (mapped.reset_time.isoformat() if mapped and mapped.reset_time else None),
            } if mapped else None,
        }
        return out
