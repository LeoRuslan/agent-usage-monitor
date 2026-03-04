#!/usr/bin/env python3
"""Main entry point for usage monitor."""

from __future__ import annotations

import argparse
import json
from typing import Optional

from rich.console import Console

from collector import ProviderCollector
from providers.registry import list_provider_choices
from ui import render_snapshots


def main(
    provider: Optional[str] = None,
    output_format: str = "rich",
    timeout: float = 8.0,
    refresh_interval: int = 240,
    verbose: bool = False,
) -> None:
    """
    Run the usage monitor.
    """
    collector = ProviderCollector(
        timeout=timeout,
        verbose=verbose,
        refresh_interval_seconds=refresh_interval,
    )
    selected = None if provider is None else [provider]
    snapshots = collector.collect(selected)

    if output_format == "json":
        payload = {provider_id: snapshot.to_dict() for provider_id, snapshot in snapshots.items()}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    console = Console()
    render_snapshots(console, snapshots.values())


if __name__ == "__main__":
    choices = list_provider_choices()
    parser = argparse.ArgumentParser(
        description="Monitor usage quotas for AI providers."
    )
    parser.add_argument(
        "--provider",
        choices=choices,
        default="all",
        help=f"Provider to check: {', '.join(choices)} (default: all)",
    )
    parser.add_argument(
        "--format",
        choices=["rich", "json"],
        default="rich",
        help="Output format (default: rich)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Per-provider timeout in seconds (default: 8.0)",
    )
    parser.add_argument(
        "--refresh-interval",
        type=int,
        default=240,
        help="Next-update interval in seconds (default: 240)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )
    args = parser.parse_args()

    provider = None if args.provider == "all" else args.provider
    main(
        provider=provider,
        output_format=args.format,
        timeout=args.timeout,
        refresh_interval=args.refresh_interval,
        verbose=args.verbose,
    )
