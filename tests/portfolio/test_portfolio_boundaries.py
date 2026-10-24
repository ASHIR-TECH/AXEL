"""Architectural boundary tests for the Phase 11 package."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "axel" / "portfolio"
FORBIDDEN_LLM_ROOTS = {
    "langchain",
    "langchain_core",
    "langchain_community",
    "langgraph",
    "openai",
    "anthropic",
    "google",
    "groq",
    "cohere",
    "transformers",
    "llama_index",
    "ollama",
    "litellm",
    "mistralai",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_portfolio_has_no_execution_or_llm_imports() -> None:
    violations: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        for module in _imports(path):
            root = module.split(".")[0]
            if module == "axel.execution" or module.startswith("axel.execution."):
                violations.append(f"{path.name}: {module}")
            if root in FORBIDDEN_LLM_ROOTS:
                violations.append(f"{path.name}: {module}")
    assert not violations, f"forbidden imports: {violations}"


def test_portfolio_does_not_write_orders() -> None:
    forbidden_tokens = (
        "submit_order(",
        "place_order(",
        "create_order(",
        "send_order(",
        "broker.submit",
        "execution_client",
    )
    violations: list[str] = []
    for path in sorted(PACKAGE.glob("*.py")):
        content = path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            if token in content:
                violations.append(f"{path.name}: {token}")
    assert not violations, f"order-submission tokens found: {violations}"
