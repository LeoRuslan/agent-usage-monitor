"""Rich UI rendering for quota display."""

from typing import Any, Dict

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from utils import try_parse_time, create_usage_bar, format_time_remaining


def render_antigravity(console: Console, result: Dict[str, Any]) -> None:
    """Render Antigravity quota results as a Rich table."""
    console.print(Panel("[bold blue]Antigravity (IDE)[/bold blue]", expand=False, border_style="blue"))
    
    if not result.get("ok"):
        console.print(f"[bold red]Error:[/bold red] {result.get('reason')}")
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", show_lines=True)
    table.add_column("Model / Label")
    table.add_column("Usage Quota", justify="left")
    table.add_column("Reset Time", style="dim")
    table.add_column("Time Left", style="bold yellow")

    items = result.get("items", [])
    
    # Sort by quota ascending, then by label
    items.sort(key=lambda x: (x.get("remaining_fraction") or 0.0, (x.get("label") or "").lower()))
    
    for it in items:
        label = it.get('label')
        frac = it.get('remaining_fraction')
        
        reset_str = it.get('reset_time')
        reset_dt = None
        if reset_str:
            reset_dt = try_parse_time(reset_str)

        time_left = format_time_remaining(reset_dt)
        
        reset_display = "-"
        if reset_dt:
            local_dt = reset_dt.astimezone()
            reset_display = local_dt.strftime("%Y-%m-%d %H:%M:%S")

        bar_str = create_usage_bar(frac)
        table.add_row(label, bar_str, reset_display, time_left)

    console.print(table)
    console.print()


def _format_model_name(model_id: str) -> str:
    """Convert model ID to human-readable name."""
    # e.g., "gemini-2.5-pro" -> "Gemini 2.5 Pro"
    # e.g., "gemini-3-flash-preview" -> "Gemini 3 Flash (Preview)"
    name = model_id.replace("gemini-", "Gemini ")
    name = name.replace("-preview", " (Preview)")
    name = name.replace("-lite", " Lite")
    name = name.replace("-pro", " Pro")
    name = name.replace("-flash", " Flash")
    # Clean up double spaces
    name = " ".join(name.split())
    return name


def render_gemini_cli(console: Console, result: Dict[str, Any]) -> None:
    """Render Gemini CLI quota results."""
    console.print(Panel("[bold magenta]Gemini CLI[/bold magenta]", expand=False, border_style="magenta"))

    if not result.get("ok"):
        console.print(f"[bold red]Error:[/bold red] {result.get('reason')}")
        return

    method = result.get("method")
    parsed = result.get("parsed")
    
    # Show method
    console.print(f"   [dim]Method:[/dim] {method}")
    
    if parsed:
        model_quotas = parsed.get("model_quotas", [])
        
        if model_quotas:
            # Show detailed model table
            table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", show_lines=True)
            table.add_column("Model")
            table.add_column("Remaining Quota", justify="left")
            table.add_column("Reset Time", style="dim")
            table.add_column("Time Left", style="bold yellow")
            
            # Sort by remaining fraction (lowest first = most used)
            model_quotas_sorted = sorted(model_quotas, key=lambda x: (x.get("remaining_fraction") or 0.0, x.get("model_id", "")))
            
            for q in model_quotas_sorted:
                model_name = _format_model_name(q.get("model_id", "Unknown"))
                frac = q.get("remaining_fraction")
                reset_str = q.get("reset_time")
                
                reset_dt = None
                if reset_str:
                    reset_dt = try_parse_time(reset_str)
                
                time_left = format_time_remaining(reset_dt)
                
                reset_display = "-"
                if reset_dt:
                    local_dt = reset_dt.astimezone()
                    reset_display = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                
                bar_str = create_usage_bar(frac)
                table.add_row(model_name, bar_str, reset_display, time_left)
            
            console.print(table)
        else:
            # Fallback to legacy single-value display
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
            
            grid = Table.grid(padding=(0, 2))
            grid.add_column(style="bold white", justify="right")
            grid.add_column(style="white")
            
            bar_str = create_usage_bar(rem, width=30)
            grid.add_row("Remaining:", bar_str)
            grid.add_row("Reset Time:", f"{reset_display}  ([bold yellow]in {time_left}[/bold yellow])")
            console.print(grid)
    else:
        console.print("[yellow]Raw output (parsing failed)[/yellow]")
        
    if not parsed and (result.get("raw") or result.get("raw_text")):
        console.print(Panel(str(result.get("raw") or result.get("raw_text"))[:500], title="Raw Output", border_style="dim"))
    
    console.print()


