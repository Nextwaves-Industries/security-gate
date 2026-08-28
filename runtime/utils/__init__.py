"""Runtime utilities exposed by the Windows RFID portal."""

from pathlib import Path

_native_package = Path(__file__).resolve().parents[2] / "package" / __name__
if _native_package.is_dir():
    __path__.append(str(_native_package))

from .serial_utils import (
    ConnectionParams,
    ReaderProtocol,
    SensorMode,
    SerialManager,
    detect_rfid_reader_port,
    get_available_port_options,
    get_available_ports,
)
from .nr155_sensor_cdc import (
    Nr155SensorCdc,
    Nr155SensorEvent,
    is_nr155_reader_port,
    is_nr155_sensor_port,
    parse_sensor_event,
)
from .zk_protocol import ZKProtocol


def __getattr__(name: str):
    """Keep desktop-only helpers lazy so headless imports never load Qt/openpyxl."""
    if name == "ExcelExporter":
        from .export_utils import ExcelExporter

        return ExcelExporter
    if name in {"UIConfig", "get_ui_config", "is_small_screen"}:
        from .ui_config import UIConfig, get_ui_config, is_small_screen

        return {
            "UIConfig": UIConfig,
            "get_ui_config": get_ui_config,
            "is_small_screen": is_small_screen,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ConnectionParams",
    "ExcelExporter",
    "ReaderProtocol",
    "Nr155SensorCdc",
    "Nr155SensorEvent",
    "SensorMode",
    "SerialManager",
    "UIConfig",
    "ZKProtocol",
    "detect_rfid_reader_port",
    "get_available_ports",
    "get_available_port_options",
    "get_ui_config",
    "is_small_screen",
    "is_nr155_reader_port",
    "is_nr155_sensor_port",
    "parse_sensor_event",
]
