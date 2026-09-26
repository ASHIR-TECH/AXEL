from unittest.mock import patch

import pytest

from scripts.run_paper_soak import validate_paper_mode


def test_runner_rejects_non_paper_mode() -> None:
    with patch("scripts.run_paper_soak.settings.environment", "live"), pytest.raises(
        RuntimeError, match="Paper soak refuses"
    ):
        validate_paper_mode()


def test_runner_accepts_safe_default_configuration() -> None:
    with patch("scripts.run_paper_soak.settings.environment", "paper"), patch(
        "scripts.run_paper_soak.settings.alpaca_paper", True
    ):
        validate_paper_mode()
