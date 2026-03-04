"""Rich UI rendering for normalized provider snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from models import ProviderSnapshot, QuotaItem
from utils import create_usage_bar, format_time_remaining

APP_VERSION = "0.1.0"


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 0.01:
        return str(int(round(value)))
    return f"{value:.1f}"


def _format_remaining(item: QuotaItem) -> str:
    if item.unit == "percent":
        if item.remaining_value is not None:
            return f"{_format_number(item.remaining_value)}% left"
        if item.remaining_fraction is not None:
            return f"{_format_number(item.remaining_fraction * 100)}% left"
    elif item.unit == "credits":
        if item.remaining_value is not None:
            return f"{_format_number(item.remaining_value)} credits left"
    elif item.unit == "usd":
        if item.remaining_value is not None:
            return f"${_format_number(item.remaining_value)} left"
    elif item.unit == "tokens":
        if item.remaining_value is not None:
            return f"{_format_number(item.remaining_value)} tokens left"
    return "Unknown"


def _format_reset(reset_at: datetime | None) -> str:
    if not reset_at:
        return "-"
    return f"Resets in {format_time_remaining(reset_at)}"


def render_snapshot(console: Console, snapshot: ProviderSnapshot) -> None:
    """Render a single provider snapshot."""
    title = f"[bold]{snapshot.name}[/bold]"
    if snapshot.plan:
        title += f" [dim]({snapshot.plan})[/dim]"
    if snapshot.meta.get("stale"):
        title += " [yellow][cached][/yellow]"
    console.print(Panel(title, expand=False, border_style="cyan"))

    if not snapshot.ok:
        console.print(f"[bold red]Error:[/bold red] {snapshot.error or 'unknown error'}")
        console.print()
        return

    if not snapshot.items:
        console.print("[yellow]No quota items found.[/yellow]")
        console.print()
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("Quota")
    table.add_column("Usage", justify="left")
    table.add_column("Status", style="dim")

    sorted_items = sorted(snapshot.items, key=lambda item: (item.remaining_fraction or 0.0, item.label.lower()))
    for item in sorted_items:
        bar_str = create_usage_bar(item.remaining_fraction)
        left_text = _format_remaining(item)
        reset_text = _format_reset(item.reset_at)
        table.add_row(item.label, f"{bar_str}\n[dim]{left_text}[/dim]", reset_text)

    console.print(table)
    console.print()


def _render_footer(console: Console, snapshots: list[ProviderSnapshot]) -> None:
    """Render global footer with version and next update."""
    next_updates = [s.next_update for s in snapshots if s.next_update]
    next_update_text = ""
    if next_updates:
        earliest = min(next_updates)
        remaining = format_time_remaining(earliest)
        if remaining:
            next_update_text = f" │ Next update in {remaining}"

    console.print(f"[dim]v{APP_VERSION}{next_update_text}[/dim]")


def render_snapshots(console: Console, snapshots: Iterable[ProviderSnapshot]) -> None:
    """Render all provider snapshots."""
    snapshot_list = list(snapshots)
    for snapshot in snapshot_list:
        render_snapshot(console, snapshot)
    _render_footer(console, snapshot_list)

