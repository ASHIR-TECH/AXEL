.PHONY: check test boundary

check:
	.venv/bin/python -m ruff check .
	.venv/bin/python scripts/check_no_llm_imports.py
	.venv/bin/python -m pytest -q

test:
	.venv/bin/python -m pytest -q

boundary:
	.venv/bin/python scripts/check_no_llm_imports.py
