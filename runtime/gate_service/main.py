"""Container entry point for the Nextwaves headless gate service."""

from __future__ import annotations

import asyncio
import logging

from .application import backup_database, run_service, validate_platform
from .observability import JsonFormatter, configure_stdout_logging
from .settings import GateServiceSettings, SettingsError


log = logging.getLogger("gate_service")

# Preserve the original bootstrap imports for internal callers while keeping
# the executable module intentionally small.
__all__ = [
    "JsonFormatter",
    "backup_database",
    "configure_stdout_logging",
    "main",
    "run_service",
    "validate_platform",
]


def main() -> int:
    configure_stdout_logging()
    try:
        validate_platform()
        settings = GateServiceSettings.from_env()
        asyncio.run(run_service(settings))
        return 0
    except (SettingsError, RuntimeError) as exc:
        log.error("Gate service cannot start: %s", exc)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        log.exception("Gate service crashed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
