#!/usr/bin/env python3
from __future__ import annotations

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

import os
import re
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psutil
import requests
from dateutil import parser as dateparser

# Silence local TLS warnings (local self-signed)
requests.packages.urllib3.disable_warnings()

# Paths / endpoints
ANTIGRAVITY_GETUNLEASH_PATH = "/exa.language_server_pb.LanguageServerService/GetUnleashData"
ANTIGRAVITY_GETUSERSTATUS_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
ANTIGRAVITY_GETCOMMANDMODELCONFIGS_PATH = "/exa.language_server_pb.LanguageServerService/GetCommandModelConfigs"

GEMINI_QUOTA_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota"
DEFAULT_GEMINI_CREDS = os.path.expanduser("~/.gemini/oauth_creds.json")
DEFAULT_GEMINI_SETTINGS = os.path.expanduser("~/.gemini/settings.json")


# ==== Utilities ====
def try_parse_time(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(int(v), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(v, str):
        try:
            return dateparser.parse(v)
        except Exception:
            try:
                return datetime.fromtimestamp(int(float(v)), tz=timezone.utc)
            except Exception:
                return None
    return None


def pretty_pct(remaining_fraction: Optional[float]) -> str:
    if remaining_fraction is None:
        return "?"
    try:
        return f"{remaining_fraction * 100:.1f}%"
    except Exception:
        return "?"


def get_color_for_fraction(fraction: Optional[float]) -> str:
    if fraction is None:
        return "white"
    if fraction > 0.7:
        return "green"
    if fraction > 0.3:
        return "yellow"
    return "red"

def create_usage_bar(fraction: Optional[float], width: int = 20) -> str:
    if fraction is None:
        return "[grey]?[/grey]"
    
    pct = int(fraction * 100)
    # Clamp percentage
    pct = max(0, min(100, pct))
    
    filled_len = int(width * (pct / 100))
    empty_len = width - filled_len
    
    color = get_color_for_fraction(fraction)
    bar = "█" * filled_len + "░" * empty_len
    return f"[{color}]{bar}[/{color}] {pct}%"


def format_time_remaining(target_dt: Optional[datetime]) -> str:
    if not target_dt:
        return ""
    
    now = datetime.now(timezone.utc)
    # Ensure target_dt is offset-aware or assume UTC
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)
        
    diff = target_dt - now
    if diff.total_seconds() < 0:
        return "[dim]Passed[/dim]"
        
    seconds = int(diff.total_seconds())
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
        
    if not parts:
        return "< 1m"
        
    return " ".join(parts)


# ==== Antigravity probe ====
@dataclass
class AntigravityQuotaItem:
    label: str
    remaining_fraction: Optional[float]
    reset_time: Optional[datetime]


class AntigravityProbe:
    def __init__(self, timeout: float = 8.0, verify_ssl: bool = False, verbose: bool = False):
        self.timeout = timeout
        self.session = requests.Session()
        self.verify_ssl = verify_ssl
        self.verbose = verbose

    def find_process(self) -> Optional[psutil.Process]:
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
        m = re.search(rf"{re.escape(flag)}(?:=|\s+)([^\s]+)", cmdline)
        return m.group(1) if m else None

    def get_listening_ports(self, pid: int) -> List[int]:
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


# ==== Gemini probe ====
class GeminiProbe:
    def __init__(self, timeout: float = 8.0, gemini_cli: Optional[str] = None, verbose: bool = False):
        self.timeout = timeout
        self.gemini_cli = gemini_cli or os.environ.get("GEMINI_CLI_PATH", "gemini")
        self.session = requests.Session()
        self.verbose = verbose

    @staticmethod
    def _read_json_file(path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def try_api_quota(self, access_token: str) -> Optional[Dict[str, Any]]:
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


# ==== Main runnable logic ====
def main(provider: Optional[str] = None):
    # Simplification: run both probes by default, no verbose output unless modified here
    verbose = False
    json_output = False
    
    results: Dict[str, Any] = {}
    
    # Run Antigravity if provider is None (all) or specifically "antigravity"
    if provider is None or provider == "antigravity":
        ag = AntigravityProbe(verbose=verbose)
        try:
            results["antigravity"] = ag.run()
        except Exception as e:
            results["antigravity"] = {"ok": False, "reason": f"exception: {e}"}

    # Run Gemini if provider is None (all) or specifically "gemini"
    if provider is None or provider == "gemini":
        gemini_cli = os.environ.get("GEMINI_CLI_PATH", None)
        gm = GeminiProbe(gemini_cli=gemini_cli, verbose=verbose)
        try:
            results["gemini"] = gm.run()
        except Exception as e:
            results["gemini"] = {"ok": False, "reason": f"exception: {e}"}

    if json_output:
        print(json.dumps(results, indent=2, default=str))
        return

    # ==== Rich Output ====
    console = Console()

    # --- Antigravity ---
    if "antigravity" in results:
        ag = results["antigravity"]
        console.print(Panel("[bold blue]Antigravity (IDE)[/bold blue]",  expand=False, border_style="blue"))
        
        if not ag.get("ok"):
            console.print(f"[bold red]Error:[/bold red] {ag.get('reason')}")
        else:
            # show_lines=True adds a separator line between every row
            table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", show_lines=True)
            table.add_column("Model / Label")
            table.add_column("Usage Quota", justify="left")
            table.add_column("Reset Time", style="dim")
            table.add_column("Time Left", style="bold yellow")

            items = ag.get("items", [])
            mapped = ag.get("mapped_primary")
            
            # Determine primary label
            primary_label = mapped.get("label") if mapped else None

            # Sort ALL items by quota (remaining_fraction) ascending, then by label (case-insensitive)
            items.sort(key=lambda x: (x.get("remaining_fraction") or 0.0, (x.get("label") or "").lower()))
            
            for it in items:
                label = it.get('label')
                
                frac = it.get('remaining_fraction')
                
                # Parse reset time back to datetime for calculation if it's a string
                reset_str = it.get('reset_time')
                reset_dt = None
                if reset_str:
                     reset_dt = try_parse_time(reset_str)

                time_left = format_time_remaining(reset_dt)
                
                reset_display = "-"
                if reset_dt:
                    # Convert to local time
                    local_dt = reset_dt.astimezone()
                    # Format nicely: HH:MM:SS or YYYY-MM-DD HH:MM:SS
                    # Since it's usually soon, HH:MM:SS might be enough, but let's do short date if needed
                    reset_display = local_dt.strftime("%H:%M:%S")

                bar_str = create_usage_bar(frac)
                table.add_row(label, bar_str, reset_display, time_left)

            console.print(table)
            console.print()

    # --- Gemini ---
    if "gemini" in results:
        gm = results["gemini"]
        # Use provider name in panel title if possible, though strict logic is above
        console.print(Panel("[bold magenta]Gemini API / CLI[/bold magenta]", expand=False, border_style="magenta"))

        if not gm.get("ok"):
            console.print(f"[bold red]Error:[/bold red] {gm.get('reason')}")
        else:
            method = gm.get("method")
            parsed = gm.get("parsed")
            
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="bold white", justify="right")
            grid.add_column(style="white")

            grid.add_row("Method:", method)
            
            if parsed:
                rem = parsed.get("remaining_fraction")
                reset_str = parsed.get("reset_time")
                
                reset_dt = None
                if reset_str:
                     reset_dt = try_parse_time(reset_str)
                
                time_left = format_time_remaining(reset_dt)
                
                reset_display = "N/A"
                if reset_dt:
                    local_dt = reset_dt.astimezone()
                    reset_display = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                
                bar_str = create_usage_bar(rem, width=30)
                grid.add_row("Remaining:", bar_str)
                grid.add_row("Reset Time:", f"{reset_display}  ([bold yellow]in {time_left}[/bold yellow])")
            else:
                grid.add_row("Status:", "[yellow]Raw output (parsing failed)[/yellow]")
                
            console.print(grid)
            
            if not parsed and (gm.get("raw") or gm.get("raw_text")):
                 console.print(Panel(str(gm.get("raw") or gm.get("raw_text"))[:500], title="Raw Output", border_style="dim"))
        
        console.print()


if __name__ == "__main__":
    
    main(provider="antigravity")
