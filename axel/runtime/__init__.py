"""Deterministic paper runtime workers.

The package is deliberately split by trust boundary:

* :mod:`axel.runtime.ingest` writes durable market data only.
* :mod:`axel.runtime.agent` writes signals and *pending* proposals only.
* :mod:`axel.runtime.core` is the only module that authorizes and executes.

None of these modules require an LLM or external credentials, so the terminal
can run against real durable records in paper mode out of the box.
"""

from axel.runtime.agent import agent_once
from axel.runtime.core import CycleResult, core_once
from axel.runtime.ingest import ingest_once

__all__ = ["CycleResult", "agent_once", "core_once", "ingest_once"]
