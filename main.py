#!/usr/bin/env python3
"""Main entry point for usage monitor."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, Optional

from rich.console import Console

from antigravity import AntigravityProbe
from gemini_cli import GeminiProbe
from ui import render_antigravity, render_gemini_cli


def main(provider: Optional[str] = None) -> None:
    """
    Run the usage monitor.
    
    Args:
        provider: Optional provider filter - "antigravity", "gemini", or None for both.
    """
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

    # Run Gemini CLI if provider is None (all) or specifically "gemini_cli"
    if provider is None or provider == "gemini_cli":
        gemini_cli = os.environ.get("GEMINI_CLI_PATH", None)
        gm = GeminiProbe(gemini_cli=gemini_cli, verbose=verbose)
        try:
            results["gemini_cli"] = gm.run()
        except Exception as e:
            results["gemini_cli"] = {"ok": False, "reason": f"exception: {e}"}

    if json_output:
        print(json.dumps(results, indent=2, default=str))
        return

    # Rich Output
    console = Console()

    if "antigravity" in results:
        render_antigravity(console, results["antigravity"])

    if "gemini_cli" in results:
        render_gemini_cli(console, results["gemini_cli"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Monitor usage quotas for AI providers (Antigravity, Gemini)"
    )
    parser.add_argument(
        "--provider",
        choices=["antigravity", "gemini_cli", "all"],
        default="antigravity",
        help="Provider to check: antigravity, gemini_cli, or all (default: antigravity)"
    )
    args = parser.parse_args()
    
    # Map "all" to None (runs both providers)
    provider = None if args.provider == "all" else args.provider
    main(provider=provider)
