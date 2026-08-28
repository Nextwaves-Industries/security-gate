"""Core application package for the Nextwaves RFID Portal."""

from pathlib import Path

_native_package = Path(__file__).resolve().parents[2] / "package" / __name__
if _native_package.is_dir():
    __path__.append(str(_native_package))

from .calibration import CalibrationService, CalibrationState
from .config import (
    AdminConfig,
    ApiConfig,
    AppConfig,
    CalibrationConfig,
    ConfigStore,
    DetectionConfig,
    MqttSdkConfig,
    ResultDeliveryConfig,
)
from .domain import (
    Direction,
    OperationType,
    PassageStatus,
    ReconciliationStatus,
    TransactionStatus,
)
from .repository import SqliteRepository
from .result_delivery import DeliveryReport, TransactionResultPublisher
from .mqtt_sdk import MqttSdkTransport
from .mqtt_credentials import MqttCredentialError, MqttCredentialStore
from .logging_config import configure_logging
from .service import InventoryService
from .signals import normalize_rssi_dbm


def __getattr__(name: str):
    """Load the runtime lazily so the flat customer config can import config types."""
    if name == "ApplicationRuntime":
        from .runtime import ApplicationRuntime

        return ApplicationRuntime
    if name in {
        "export_inventory_xlsx",
        "export_raw_read_history_xlsx",
        "export_transaction_xlsx",
    }:
        from .exporter import (
            export_inventory_xlsx,
            export_raw_read_history_xlsx,
            export_transaction_xlsx,
        )

        return {
            "export_inventory_xlsx": export_inventory_xlsx,
            "export_raw_read_history_xlsx": export_raw_read_history_xlsx,
            "export_transaction_xlsx": export_transaction_xlsx,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AppConfig",
    "AdminConfig",
    "ApiConfig",
    "ApplicationRuntime",
    "CalibrationConfig",
    "CalibrationService",
    "CalibrationState",
    "export_transaction_xlsx",
    "export_inventory_xlsx",
    "export_raw_read_history_xlsx",
    "configure_logging",
    "ConfigStore",
    "DetectionConfig",
    "MqttSdkConfig",
    "MqttSdkTransport",
    "MqttCredentialError",
    "MqttCredentialStore",
    "DeliveryReport",
    "Direction",
    "InventoryService",
    "normalize_rssi_dbm",
    "OperationType",
    "PassageStatus",
    "ReconciliationStatus",
    "ResultDeliveryConfig",
    "SqliteRepository",
    "TransactionStatus",
    "TransactionResultPublisher",
]
