"""Base provider for Codeium-based language servers."""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional

import psutil
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from models import ProviderSnapshot, QuotaItem
from providers.base_provider import BaseProvider
from utils import try_parse_time


class LanguageServerProvider(BaseProvider):
    """Base for providers that probe Codeium language servers."""

    process_markers: tuple[str, ...] = ()

    LS_UNLEASH_PATH = "/exa.language_server_pb.LanguageServerService/GetUnleashData"
    LS_STATUS_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
    LS_FALLBACK_PATH = "/exa.language_server_pb.LanguageServerService/GetCommandModelConfigs"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = requests.Session()

    def _find_ls_process(self, markers: tuple[str, ...] | None = None) -> Optional[psutil.Process]:
        """Find language server process matching given or configured markers."""
        search_markers = markers or self.process_markers
        for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if "language_server" not in name and "language_server" not in cmdline:
                    continue
                if any(marker in cmdline for marker in search_markers):
                    return proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    @staticmethod
    def _extract_flag(cmdline: str, flag: str) -> Optional[str]:
        match = re.search(rf"{re.escape(flag)}(?:=|\s+)([^\s]+)", cmdline)
        return match.group(1) if match else None

    def _get_listening_ports(self, pid: int) -> list[int]:
        ports: set[int] = set()
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN and conn.pid == pid and conn.laddr:
                    ports.add(conn.laddr.port)
        except Exception:
            pass
        if not ports:
            try:
                output = subprocess.check_output(
                    ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-p", str(pid)],
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                for line in output.splitlines():
                    m = re.search(r":(\d+)\s+\(LISTEN\)", line)
                    if m:
                        ports.add(int(m.group(1)))
            except Exception:
                pass
        return sorted(ports)

    def _probe_port(self, ports: list[int], csrf_token: str) -> Optional[tuple[int, str]]:
        headers = {
            "x-codeium-csrf-token": csrf_token,
            "Connect-Protocol-Version": "1",
            "Content-Type": "application/json",
        }
        for port in ports:
            for scheme in ("http", "https"):
                try:
                    # Try GetUserStatus endpoint instead of GetUnleashData
                    resp = self._session.post(
                        f"{scheme}://127.0.0.1:{port}{self.LS_STATUS_PATH}",
                        headers=headers, json={"metadata": {}}, timeout=2.0,
                        verify=False,
                    )
                    if resp.status_code in [200, 400, 401]:  # Any response means endpoint exists
                        return (port, scheme)
                except requests.RequestException:
                    continue
        return None

    def _fetch_status(
        self, port: int, scheme: str, csrf_token: str,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        headers = {
            "x-codeium-csrf-token": csrf_token,
            "Content-Type": "application/json",
        }
        metadata = {
            "ideName": self.provider_id,
            "extensionName": self.provider_id,
            "locale": "en",
            "ideVersion": "unknown",
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        payload = {"metadata": metadata}
        for path in (self.LS_STATUS_PATH, self.LS_FALLBACK_PATH):
            try:
                resp = self._session.post(
                    f"{scheme}://127.0.0.1:{port}{path}",
                    headers=headers, json=payload, timeout=self.timeout,
                    verify=False,
                )
                if resp.status_code == 200:
                    return resp.json()
            except requests.RequestException:
                continue
        return None

    def _parse_ls_items(self, status: Dict[str, Any]) -> list[QuotaItem]:
        configs = (
            status.get("userStatus", {})
            .get("cascadeModelConfigData", {})
            .get("clientModelConfigs", [])
        )
        items: list[QuotaItem] = []
        if not isinstance(configs, list):
            return items
        for idx, cfg in enumerate(configs):
            label = cfg.get("label") or cfg.get("modelLabel") or f"Model {idx + 1}"
            quota = cfg.get("quotaInfo") or {}
            raw_fraction = quota.get("remainingFraction")
            try:
                fraction = float(raw_fraction) if raw_fraction is not None else None
            except (TypeError, ValueError):
                fraction = None
            if fraction is not None:
                fraction = max(0.0, min(1.0, fraction))
            items.append(
                QuotaItem(
                    id=f"{self.provider_id}:ls:{idx}",
                    label=str(label),
                    unit="percent",
                    remaining_value=(fraction * 100.0 if fraction is not None else None),
                    remaining_fraction=fraction,
                    limit_value=100.0,
                    reset_at=try_parse_time(quota.get("resetTime")),
                )
            )
        return items

    def collect_from_language_server(self, extra_metadata: Optional[Dict[str, Any]] = None) -> ProviderSnapshot:
        """Full LS probing flow."""
        process = self._find_ls_process()
        if not process:
            return self.failure_snapshot(
                f"{self.display_name} language_server process not found"
            )
        try:
            cmdline = " ".join(process.cmdline())
        except Exception:
            cmdline = ""

        csrf_token = self._extract_flag(cmdline, "--csrf_token")
        if not csrf_token:
            return self.failure_snapshot(
                f"{self.display_name} language_server found, but --csrf_token missing"
            )

        ports = self._get_listening_ports(process.pid)
        ext_port_str = self._extract_flag(cmdline, "--extension_server_port")
        if ext_port_str:
            try:
                ext_port = int(ext_port_str)
                ports = [ext_port] + [p for p in ports if p != ext_port]
            except ValueError:
                pass

        if not ports:
            return self.failure_snapshot(
                f"{self.display_name} language_server found, but no listening ports"
            )

        result = self._probe_port(ports, csrf_token)
        if not result:
            return self.failure_snapshot(
                f"{self.display_name} language_server detected, but no port responded"
            )
        connect_port, scheme = result

        status = self._fetch_status(connect_port, scheme, csrf_token, extra_metadata)
        if not status:
            return self.failure_snapshot(
                f"{self.display_name} language_server responded, but status endpoint failed"
            )

        items = self._parse_ls_items(status)
        if not items:
            return self.failure_snapshot(
                f"{self.display_name} status parsed but no quota items found"
            )

        return self.success_snapshot(
            items=items,
            meta={"source": "language_server", "pid": process.pid, "connect_port": connect_port},
        )
