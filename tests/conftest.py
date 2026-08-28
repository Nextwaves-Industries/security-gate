"""Test harness for the plaintext transport adapters.

Only ``gate_service.api.rest`` and ``gate_service.api.grpc_server`` are source
in this release repository; everything they call lives in compiled, Linux-only
``rfid_portal`` / ``gate_service`` modules. ``rfid_portal/__init__.py`` imports
those binaries eagerly, so the whole package is replaced with lightweight stubs
*before* the adapters are imported. The stubs expose only the names the
adapters actually use.
"""

from __future__ import annotations

from enum import Enum
import json
from pathlib import Path
import sys
import threading
import types
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME))


# --------------------------------------------------------------------------
# Stubs for compiled rfid_portal modules
# --------------------------------------------------------------------------
class TransactionStatus(str, Enum):
    OPEN = "OPEN"
    ACTIVE = "ACTIVE"
    COMMITTED = "COMMITTED"
    CANCELLED = "CANCELLED"


class ProtectedStateError(Exception):
    pass


class RemoteCommandFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class RemoteCommandExecutor:
    def __init__(self, runtime, control, *, timeout_s: float):
        self.runtime = runtime
        self.control = control
        self.timeout_s = timeout_s
        self.calls: list[dict[str, Any]] = []
        self.raise_failure: RemoteCommandFailure | None = None

    def execute(self, command, payload, actor, request_id, key, *, require_idempotency):
        self.calls.append(
            {
                "command": command,
                "payload": payload,
                "actor": actor,
                "request_id": request_id,
                "key": key,
                "thread": threading.get_ident(),
            }
        )
        if self.raise_failure is not None:
            raise self.raise_failure
        return {"command": command, "accepted": True, "result": {}}


def validate_actor(actor: str) -> str:
    if not actor:
        raise RemoteCommandFailure("command_rejected", "actor required")
    return actor


def validate_idempotency_key(key: str, required: bool = False) -> str:
    if required and not key:
        raise RemoteCommandFailure("command_rejected", "idempotency key required")
    return key


def _install_stubs() -> None:
    if "rfid_portal" in sys.modules and getattr(sys.modules["rfid_portal"], "_stub", False):
        return
    pkg = types.ModuleType("rfid_portal")
    pkg.__path__ = []  # type: ignore[attr-defined]
    pkg._stub = True  # type: ignore[attr-defined]
    domain = types.ModuleType("rfid_portal.domain")
    domain.TransactionStatus = TransactionStatus  # type: ignore[attr-defined]
    protected = types.ModuleType("rfid_portal.protected_state")
    protected.ProtectedStateError = ProtectedStateError  # type: ignore[attr-defined]
    remote = types.ModuleType("rfid_portal.remote_commands")
    remote.RemoteCommandExecutor = RemoteCommandExecutor  # type: ignore[attr-defined]
    remote.RemoteCommandFailure = RemoteCommandFailure  # type: ignore[attr-defined]
    remote.validate_actor = validate_actor  # type: ignore[attr-defined]
    remote.validate_idempotency_key = validate_idempotency_key  # type: ignore[attr-defined]
    sys.modules.update(
        {
            "rfid_portal": pkg,
            "rfid_portal.domain": domain,
            "rfid_portal.protected_state": protected,
            "rfid_portal.remote_commands": remote,
        }
    )


_install_stubs()


# --------------------------------------------------------------------------
# Fakes for the composition-root objects handed to create_app()
# --------------------------------------------------------------------------
class FakeSettings:
    def __init__(self, *, development: bool = False, max_body_bytes: int = 1024):
        self.development = development
        self.max_body_bytes = max_body_bytes
        self.command_timeout_s = 1.0
        self.grpc_host = "127.0.0.1"
        self.grpc_port = 0
        self.allow_insecure = True
        self.tls_cert_file = ""
        self.tls_key_file = ""

    def api_token(self) -> str:
        return "test-token"


class FakeRepository:
    """In-memory repository that records the thread each call ran on."""

    def __init__(self):
        self.transactions: dict[str, dict[str, Any]] = {
            "tx-1": {"transaction_id": "tx-1", "status": "COMMITTED"}
        }
        self.threads: dict[str, int] = {}
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail_with: Exception | None = None
        self.commands: dict[str, dict[str, Any]] = {}
        self.audits: list[tuple] = []

    def _record(self, name, *args, **kwargs):
        self.threads[name] = threading.get_ident()
        self.calls.append((name, args, kwargs))
        if self.fail_with is not None:
            raise self.fail_with

    def list_transactions(self, *, status, limit, offset):
        self._record("list_transactions", status=status, limit=limit, offset=offset)
        return list(self.transactions.values())

    def get_transaction_record(self, transaction_id):
        self._record("get_transaction_record", transaction_id)
        return self.transactions[transaction_id]

    def transaction_reconciliation(self, transaction_id):
        self._record("transaction_reconciliation", transaction_id)
        return {"expected": 0, "seen": 0}

    def net_transaction_tags(self, transaction_id):
        self._record("net_transaction_tags", transaction_id)
        return [{"epc": "E200"}]

    def list_passages(self, transaction_id):
        self._record("list_passages", transaction_id)
        return []

    def list_transaction_audit(self, transaction_id, *, limit, offset):
        self._record("list_transaction_audit", transaction_id, limit=limit, offset=offset)
        return [{"event": "x", "payload_json": "{\"a\":1}"}, {"event": "y", "payload_json": "bad"}]

    def get_calibration(self, calibration_id):
        self._record("get_calibration", calibration_id)
        return {"calibration_id": calibration_id, "gate_id": "G1"}

    def reserve_remote_command(self, **kwargs):
        self._record("reserve_remote_command", **kwargs)
        return {"reservation_outcome": "RESERVED", "error_code": "", "error_message": "", "response": {}}

    def complete_remote_command(self, **kwargs):
        self._record("complete_remote_command", **kwargs)
        self.commands[kwargs["idempotency_key"]] = kwargs

    def mark_remote_command_reconciliation_required(self, **kwargs):
        self._record("mark_remote_command_reconciliation_required", **kwargs)

    def audit(self, *args):
        self.audits.append(args)


class FakeCalibration:
    def status(self):
        return {"state": "CALIBRATED"}

    def list_runs(self, *, limit, offset):
        return []

    def start(self, actor, notes):
        return {"calibration_id": "cal-1", "status": "OPEN", "updated_at": "now", "raw": 1}

    def evaluate(self, calibration_id, actor):
        return {"calibration_id": calibration_id, "status": "PASSED", "updated_at": "now"}

    def abort(self, calibration_id, actor, reason):
        return {"calibration_id": calibration_id, "status": "ABORTED", "updated_at": "now"}


class FakeRuntime:
    def __init__(self, repository=None, *, with_lock: bool = True):
        self.repository = repository or FakeRepository()
        self.calibration = FakeCalibration()
        self.config = types.SimpleNamespace(gate_id="G1")
        if with_lock:
            self.remote_command_lock = threading.Lock()


class FakeControl:
    def __init__(self):
        self.raise_exc: BaseException | None = None
        self.thread: int | None = None

    def status(self, timeout_s):
        self.thread = threading.get_ident()
        if self.raise_exc is not None:
            raise self.raise_exc
        return {"gate_id": "G1", "state": "READY", "ready": True, "timeout": timeout_s}


class FakeEngine:
    def __init__(self, ready: bool = True):
        self.ready = ready
        self.thread: int | None = None

    def status(self):
        self.thread = threading.get_ident()
        return {"ready": self.ready, "state": "READY" if self.ready else "CALIBRATION_REQUIRED"}

    def capture_calibration_background(self, calibration_id, *, duration_seconds, actor):
        return {"calibration_id": calibration_id, "status": "BACKGROUND", "updated_at": "now"}

    def capture_calibration_pass(self, calibration_id, *, direction, expected_epcs, timeout_seconds, actor):
        return {"calibration_id": calibration_id, "status": "PASS", "updated_at": "now"}

    def request_cancel_calibration_capture(self, message, *, calibration_id):
        return None


@pytest.fixture
def loop_thread_ids():
    """Collects the event-loop thread id via a request-time hook."""
    return {}


@pytest.fixture
def make_app():
    from fastapi.testclient import TestClient
    from gate_service.api.rest import create_app

    def _make(*, development=False, runtime=None, control=None, engine=None, max_body_bytes=1024):
        runtime = runtime or FakeRuntime()
        control = control or FakeControl()
        engine = engine or FakeEngine()
        settings = FakeSettings(development=development, max_body_bytes=max_body_bytes)
        app = create_app(runtime, control, engine, settings)
        client = TestClient(app, raise_server_exceptions=False)
        return app, client, runtime, control, engine

    return _make


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


@pytest.fixture
def command_headers(auth) -> dict[str, str]:
    return {**auth, "X-Operator-ID": "op-1", "Idempotency-Key": "key-1"}


@pytest.fixture
def contract_openapi() -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / "openapi.json").read_text(encoding="utf-8"))
