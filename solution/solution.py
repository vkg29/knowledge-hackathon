"""Submission entry point: `build()` returns the ledger-backed knowledge system.

Set ANTHROPIC_API_KEY for best results; uses Claude via the Anthropic API for question understanding.
Without a key it answers with rules only. See NOTES.md for how the system decides what is current.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(next(d / "harness" for d in HERE.parents if (d / "harness" / "contract.py").exists())))

import contract  # noqa: E402
from truthledger import ai  # noqa: E402
from truthledger.system import LedgerKnowledge  # noqa: E402


def build() -> LedgerKnowledge:
    """Prints one line saying whether the AI steps are ON or OFF (no key found)."""
    return LedgerKnowledge(contract, ai.from_environment())
