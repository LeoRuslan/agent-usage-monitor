"""Base probe class for quota monitoring."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseProbe(ABC):
    """Abstract base class for quota probes."""

    def __init__(self, timeout: float = 8.0, verbose: bool = False) -> None:
        """
        Initialize the probe.

        Args:
            timeout: Request timeout in seconds.
            verbose: Enable verbose logging.
        """
        self.timeout = timeout
        self.verbose = verbose

    @abstractmethod
    def run(self) -> Dict[str, Any]:
        """
        Execute the probe and return results.

        Returns:
            Dictionary with probe results. Must contain 'ok' key.
            If ok is False, should contain 'reason' key with error description.
        """
        pass

    def _log(self, message: str) -> None:
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            print(message)
