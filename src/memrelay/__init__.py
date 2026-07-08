"""memrelay — portable, graph-based persistent memory for AI coding agents.

memrelay is a memory-domain consumer of traceforge (PyPI ``traceforge-toolkit``,
imported as ``traceforge``). traceforge normalizes ~18 agents' session traces into
a common ``SessionEvent``; memrelay adds the memory layer (episode assembly,
Graphiti ingestion, retrieval, MCP server) below that seam.

This is the E0 foundations skeleton — see ``docs/e0-spike.md`` for the de-risking
spike that verified the Copilot ingestion path end-to-end.
"""

from __future__ import annotations

__version__ = "0.0.1"

__all__ = ["__version__"]
