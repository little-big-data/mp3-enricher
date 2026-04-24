from __future__ import annotations

import io
import logging
import os
import sys

import structlog


def configure_logging() -> None:
    # Reconfigure sys.stdout to UTF-8 so structlog's PrintLogger (and any
    # other print calls) don't crash on CP1252 Windows consoles when log
    # fields contain non-ASCII characters (e.g. ā, ü, ñ).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    elif hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

    # Check if we are in a CI environment
    is_ci = os.getenv("CI", "0").lower() in ("1", "true", "yes")

    shared_processors: list[structlog.types.Processor] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    processors: list[structlog.types.Processor]
    if is_ci:
        # JSON output for CI/Production
        processors = [
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Pretty-printed output for development
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
