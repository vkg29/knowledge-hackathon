"""Submission entry point: `build()` returns the ledger-backed knowledge system.

See NOTES.md for how the system decides what is current.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(next(d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists())))

import contract  # noqa: E402
from truthledger.system import LedgerKnowledge  # noqa: E402


def build() -> LedgerKnowledge:
    return LedgerKnowledge(contract)
