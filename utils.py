"""Utility functions for parsing and formatting."""

from datetime import datetime, timezone
from typing import Any, Optional

from dateutil import parser as dateparser


def try_parse_time(v: Any) -> Optional[datetime]:
    """Parse various time formats into datetime."""
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
    """Format fraction as percentage string."""
    if remaining_fraction is None:
        return "?"
    try:
        return f"{remaining_fraction * 100:.1f}%"
    except Exception:
        return "?"


def get_color_for_fraction(fraction: Optional[float]) -> str:
    """Get Rich color based on remaining fraction."""
    if fraction is None:
        return "white"
    if fraction > 0.7:
        return "green"
    if fraction > 0.3:
        return "yellow"
    return "red"


def create_usage_bar(fraction: Optional[float], width: int = 20) -> str:
    """Create a Rich-formatted progress bar."""
    if fraction is None:
        return "[grey]?[/grey]"
    
    pct = int(fraction * 100)
    pct = max(0, min(100, pct))
    
    filled_len = int(width * (pct / 100))
    empty_len = width - filled_len
    
    color = get_color_for_fraction(fraction)
    bar = "█" * filled_len + "░" * empty_len
    return f"[{color}]{bar}[/{color}] {pct}%"


def format_time_remaining(target_dt: Optional[datetime]) -> str:
    """Format time remaining until target datetime."""
    if not target_dt:
        return ""
    
    now = datetime.now(timezone.utc)
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
