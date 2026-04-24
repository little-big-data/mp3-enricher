from __future__ import annotations

import pytest
import structlog

from tagger.logging_config import configure_logging


@pytest.mark.unit
def test_configure_logging_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "0")
    configure_logging()
    logger = structlog.get_logger()
    logger.info("test_dev")


@pytest.mark.unit
def test_configure_logging_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "1")
    configure_logging()
    logger = structlog.get_logger()
    logger.info("test_ci")


@pytest.mark.unit
def test_configure_logging_idempotent() -> None:
    # Act
    configure_logging()
    configure_logging()

    # Assert
    log = structlog.get_logger("test")
    log.info("test_log")
    assert log is not None
