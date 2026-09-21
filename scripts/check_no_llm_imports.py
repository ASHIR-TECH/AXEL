"""
CI / Lint check to enforce the architectural boundary:
Zero LLM library imports are permitted in `axel/risk/` and `axel/execution/`.
"""

import ast
import sys
from pathlib import Path
from typing import List, Tuple

FORBIDDEN_LLM_MODULES = {
    "langchain",
    "langchain_core",
    "langchain_community",
    "langgraph",
    "openai",
    "anthropic",
    "google.generativeai",
    "groq",
    "cohere",
    "transformers",
    "llama_index",
    "ollama",
    "litellm",
    "mistralai",
}

RESTRICTED_DIRS = [
    Path("axel/risk"),
    Path("axel/execution"),
]


def check_file(file_path: Path) -> List[Tuple[int, str]]:
    """Scan an individual Python file for forbidden LLM imports."""
    violations = []
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as e:
        violations.append((0, f"Failed to parse file: {e}"))
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_module = alias.name.split(".")[0]
                if root_module in FORBIDDEN_LLM_MODULES:
                    violations.append((node.lineno, f"Forbidden direct import: {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_module = node.module.split(".")[0]
                if root_module in FORBIDDEN_LLM_MODULES:
                    violations.append((node.lineno, f"Forbidden from-import: {node.module}"))

    return violations


def main() -> int:
    # Ensure UTF-8 output on Windows consoles
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    repo_root = Path(__file__).resolve().parent.parent
    total_violations = 0

    print("Running architectural boundary lint: Verifying zero LLM imports in risk/ and execution/...")

    for restricted_dir in RESTRICTED_DIRS:
        target_dir = repo_root / restricted_dir
        if not target_dir.exists():
            continue

        for py_file in target_dir.rglob("*.py"):
            violations = check_file(py_file)
            for line, msg in violations:
                rel_path = py_file.relative_to(repo_root)
                print(f"[FAIL] ARCHITECTURAL VIOLATION in {rel_path}:{line} -> {msg}")
                total_violations += 1

    if total_violations == 0:
        print("[PASS] No forbidden LLM imports detected in risk/ or execution/.")
        return 0
    else:
        print(f"[FAIL] Detected {total_violations} forbidden LLM imports in deterministic core!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
