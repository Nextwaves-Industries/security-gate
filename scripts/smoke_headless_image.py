"""Smoke-test the protected Linux runtime from inside its container image."""

from __future__ import annotations

import importlib.util
import json
import sys

import gate_service
import gate_service.application as application
import gate_service.calibration_capture as calibration_capture
import gate_service.persistence as persistence
import gate_service.reader_engine as reader_engine
import models._protected_assets as protected_assets
import nextwaves_core


def main() -> None:
    expected_version = sys.argv[1] if len(sys.argv) > 1 else None
    if expected_version is not None:
        assert gate_service.__version__ == expected_version

    protected_modules = (
        application,
        calibration_capture,
        persistence,
        reader_engine,
        protected_assets,
        nextwaves_core,
    )
    assert all(
        (module.__file__ or "").endswith(".so")
        for module in protected_modules
    )
    for forbidden in ("PyQt6", "qfluentwidgets", "pip"):
        assert importlib.util.find_spec(forbidden) is None, forbidden

    print(
        json.dumps(
            {
                "service_version": gate_service.__version__,
                "protected_modules_checked": len(protected_modules),
                "status": "ok",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
