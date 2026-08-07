"""Compatibility import for the control-owned SQLite ledger adapter."""

from .repository import LedgerFaultInjectedError, SqliteLedger

__all__ = ["LedgerFaultInjectedError", "SqliteLedger"]
