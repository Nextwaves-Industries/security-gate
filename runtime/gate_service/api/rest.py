"""FastAPI command/query plane for one physical gate."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
import re
import uuid
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from rfid_portal.domain import TransactionStatus
from rfid_portal.protected_state import ProtectedStateError
from rfid_portal.remote_commands import (
    RemoteCommandExecutor,
    RemoteCommandFailure,
    validate_actor,
    validate_idempotency_key,
)

from .schemas import (
    AbortCalibrationRequest,
    CalibrationBackgroundRequest,
    CalibrationMutationResponse,
    CalibrationPassRequest,
    CancelTransactionRequest,
    CommandResponse,
    ErrorEnvelope,
    EvaluateCalibrationRequest,
    ItemsResponse,
    PageResponse,
    StartCalibrationRequest,
    StartInventoryRequest,
    TransactionResponse,
)


_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_BODYLESS_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}
_bearer = HTTPBearer(auto_error=False)
log = logging.getLogger("gate_service.rest")

_COMMON_ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Malformed or invalid request"},
    401: {"model": ErrorEnvelope, "description": "Authentication failed"},
    404: {"model": ErrorEnvelope, "description": "Resource not found"},
    409: {"model": ErrorEnvelope, "description": "State or idempotency conflict"},
    413: {"model": ErrorEnvelope, "description": "Request body too large"},
    422: {"model": ErrorEnvelope, "description": "Business payload is invalid"},
    500: {"model": ErrorEnvelope, "description": "Unexpected server failure"},
    503: {"model": ErrorEnvelope, "description": "Gate dependency is unavailable"},
    504: {"model": ErrorEnvelope, "description": "Hardware command timed out"},
}


class ApiFailure(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _dump(model) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _request_id(request: Request) -> str:
    """Return the request correlation id without ever raising.

    Exception handlers must be able to build an error envelope even when the
    request never reached the ``request_context`` middleware.
    """

    request_id = getattr(request.state, "request_id", None)
    if not request_id:
        request_id = f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id
    return request_id


def _finalize_headers(response, request_id: str):
    response.headers["X-Request-ID"] = request_id
    for name, value in _SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


async def _db(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking repository/engine call off the event loop."""

    return await run_in_threadpool(fn, *args, **kwargs)


def create_app(runtime, control, engine, settings) -> FastAPI:
    app = FastAPI(
        title="Nextwaves Gate Service API",
        version="1.0.0",
        docs_url="/docs" if settings.development else None,
        redoc_url=None,
        # The live schema is only public in development; production serves it
        # to authenticated clients through the explicit route registered below.
        openapi_url="/openapi.json" if settings.development else None,
        responses=_COMMON_ERROR_RESPONSES,
    )
    expected_token = settings.api_token()
    executor = RemoteCommandExecutor(
        runtime, control, timeout_s=settings.command_timeout_s
    )
    # The lock must be the same object the reader engine serialises hardware
    # mutations with. A private fallback lock would silently defeat that, so a
    # missing attribute is a startup error rather than a degraded mode.
    mutation_lock = runtime.remote_command_lock
    max_body_bytes = int(settings.max_body_bytes)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable):
        supplied = request.headers.get("X-Request-ID", "").strip()
        request.state.request_id = (
            supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else f"req_{uuid.uuid4().hex}"
        )
        request_id = request.state.request_id
        length = request.headers.get("content-length")
        if length:
            try:
                parsed_length = int(length)
                if parsed_length < 0:
                    raise ValueError
            except ValueError:
                return _error(
                    400,
                    "invalid_content_length",
                    "Content-Length is invalid",
                    request_id,
                )
            if parsed_length > max_body_bytes:
                return _error(
                    413, "request_too_large", "Request body exceeds 1 MiB", request_id
                )
        elif request.method not in _BODYLESS_METHODS:
            # Chunked transfer: Starlette caches the body so downstream
            # handlers replay it. This bounds the accepted size, not the peak
            # memory of an in-flight chunked upload.
            body = await request.body()
            if len(body) > max_body_bytes:
                return _error(
                    413, "request_too_large", "Request body exceeds 1 MiB", request_id
                )
        response = await call_next(request)
        return _finalize_headers(response, request_id)

    @app.exception_handler(ApiFailure)
    async def api_failure(request: Request, exc: ApiFailure):
        return _error(exc.status, exc.code, exc.message, _request_id(request))

    @app.exception_handler(RequestValidationError)
    async def validation_failure(request: Request, exc: RequestValidationError):
        details = exc.errors()
        message = details[0].get("msg", "Request is invalid") if details else "Request is invalid"
        request_id = _request_id(request)
        if any(detail.get("type") == "json_invalid" for detail in details):
            return _error(
                400, "invalid_json", "Request body is not valid JSON", request_id
            )
        return _error(422, "invalid_payload", str(message), request_id)

    @app.exception_handler(HTTPException)
    async def http_failure(request: Request, exc: HTTPException):
        return _error(
            exc.status_code,
            "not_found" if exc.status_code == 404 else "http_error",
            str(exc.detail),
            _request_id(request),
        )

    @app.exception_handler(Exception)
    async def unknown_failure(request: Request, exc: Exception):
        # Runs inside Starlette's ServerErrorMiddleware, outside the
        # request_context middleware, so _error() must add headers itself.
        request_id = _request_id(request)
        log.exception(
            "Unhandled REST error request_id=%s path=%s",
            request_id,
            request.url.path,
            exc_info=exc,
        )
        return _error(500, "internal_error", "Unexpected server error", request_id)

    async def authenticate(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        supplied = credentials.credentials if credentials else ""
        scheme = credentials.scheme if credentials else ""
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied, expected_token
        ):
            raise ApiFailure(401, "unauthorized", "Valid bearer token required")

    if not settings.development:

        @app.get(
            "/openapi.json",
            include_in_schema=False,
            dependencies=[Depends(authenticate)],
        )
        async def openapi_schema() -> dict[str, Any]:
            return app.openapi()

    def command_headers(
        operator_id: str = Header(..., alias="X-Operator-ID"),
        idempotency_key: str = Header(..., alias="Idempotency-Key"),
    ) -> tuple[str, str]:
        return operator_id, idempotency_key

    async def require_empty_body(request: Request) -> None:
        if (await request.body()).strip():
            raise ApiFailure(
                400,
                "unexpected_payload",
                "This command requires an empty request body",
            )

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        # Liveness is deliberately independent from devices and readiness.
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request):
        status = await _db(engine.status)
        if not status["ready"]:
            return _error(
                503,
                str(status["state"]).lower(),
                "Gate is not ready",
                _request_id(request),
            )
        return {"status": "ready", "state": status["state"]}

    @app.get("/api/v1/status", dependencies=[Depends(authenticate)])
    async def status() -> dict[str, Any]:
        return await run_in_threadpool(control.status, settings.command_timeout_s)

    async def calibration_read(operation: Callable[[], Any]) -> Any:
        try:
            return await run_in_threadpool(operation)
        except ProtectedStateError as exc:
            raise ApiFailure(
                503,
                "calibration_unavailable",
                "Calibration state is unavailable",
            ) from exc

    @app.get("/api/v1/calibration", dependencies=[Depends(authenticate)])
    async def calibration_status() -> dict[str, Any]:
        return await calibration_read(runtime.calibration.status)

    @app.get(
        "/api/v1/calibration/runs",
        dependencies=[Depends(authenticate)],
        response_model=PageResponse,
    )
    async def calibration_runs(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10_000_000),
    ):
        items = await calibration_read(
            lambda: runtime.calibration.list_runs(limit=limit, offset=offset)
        )
        return {"items": items, "limit": limit, "offset": offset}

    async def require_calibration(calibration_id: str) -> dict[str, Any]:
        run = await calibration_read(
            lambda: runtime.repository.get_calibration(calibration_id)
        )
        if run is None or run.get("gate_id") != runtime.config.gate_id:
            raise ApiFailure(404, "calibration_not_found", "Calibration not found")
        return run

    @app.get(
        "/api/v1/calibration/runs/{calibration_id}",
        dependencies=[Depends(authenticate)],
    )
    async def calibration_run(calibration_id: str) -> dict[str, Any]:
        return await require_calibration(calibration_id)

    @app.get(
        "/api/v1/transactions",
        dependencies=[Depends(authenticate)],
        response_model=PageResponse,
    )
    async def transactions(
        status: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10_000_000),
    ):
        normalized = status.upper().strip()
        if normalized:
            try:
                normalized = TransactionStatus(normalized).value
            except ValueError as exc:
                raise ApiFailure(422, "invalid_status", "Unknown transaction status") from exc
        items = await _db(
            runtime.repository.list_transactions,
            status=normalized or None,
            limit=limit,
            offset=offset,
        )
        return {"items": items, "limit": limit, "offset": offset}

    async def require_transaction(transaction_id: str) -> dict[str, Any]:
        try:
            return await _db(runtime.repository.get_transaction_record, transaction_id)
        except KeyError as exc:
            raise ApiFailure(404, "transaction_not_found", "Transaction not found") from exc

    @app.get(
        "/api/v1/transactions/{transaction_id}",
        dependencies=[Depends(authenticate)],
        response_model=TransactionResponse,
    )
    async def transaction(transaction_id: str):
        record = await require_transaction(transaction_id)
        reconciliation = await _db(
            runtime.repository.transaction_reconciliation, transaction_id
        )
        return {"transaction": record, "reconciliation": reconciliation}

    @app.get(
        "/api/v1/transactions/{transaction_id}/tags",
        dependencies=[Depends(authenticate)],
        response_model=ItemsResponse,
    )
    async def transaction_tags(transaction_id: str):
        await require_transaction(transaction_id)
        items = await _db(runtime.repository.net_transaction_tags, transaction_id)
        return {"items": items}

    @app.get(
        "/api/v1/transactions/{transaction_id}/passages",
        dependencies=[Depends(authenticate)],
        response_model=ItemsResponse,
    )
    async def transaction_passages(transaction_id: str):
        await require_transaction(transaction_id)
        items = await _db(runtime.repository.list_passages, transaction_id)
        return {"items": items}

    @app.get(
        "/api/v1/transactions/{transaction_id}/audit",
        dependencies=[Depends(authenticate)],
        response_model=PageResponse,
    )
    async def transaction_audit(
        transaction_id: str,
        limit: int = Query(default=200, ge=1, le=1000),
        offset: int = Query(default=0, ge=0, le=10_000_000),
    ):
        await require_transaction(transaction_id)
        items = await _db(
            runtime.repository.list_transaction_audit,
            transaction_id,
            limit=limit,
            offset=offset,
        )
        for item in items:
            raw = item.pop("payload_json", "{}")
            try:
                item["payload"] = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                item["payload"] = {}
        return {"items": items, "limit": limit, "offset": offset}

    async def execute_command(
        request: Request,
        command: str,
        payload: dict[str, Any],
        headers: tuple[str, str],
    ) -> dict[str, Any]:
        actor, key = headers
        request_id = _request_id(request)
        try:
            return await run_in_threadpool(
                executor.execute,
                command,
                payload,
                actor,
                request_id,
                key,
                require_idempotency=True,
            )
        except RemoteCommandFailure as exc:
            raise _map_command_failure(exc) from exc

    async def execute_calibration_mutation(
        request: Request,
        command: str,
        payload: dict[str, Any],
        headers: tuple[str, str],
        operation: Callable[[str], dict[str, Any]],
        before_lock: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        actor, key = headers
        request_id = _request_id(request)
        try:
            actor = validate_actor(actor)
            key = validate_idempotency_key(key, required=True)
        except RemoteCommandFailure as exc:
            raise _map_command_failure(exc) from exc

        fingerprint = hashlib.sha256(
            json.dumps(
                {"command": command, "payload": payload, "actor": actor},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        @contextmanager
        def bounded_mutation_lock():
            acquired = mutation_lock.acquire(timeout=settings.command_timeout_s)
            if not acquired:
                failure = ApiFailure(
                    504,
                    "calibration_command_timeout",
                    "Timed out waiting for another gate command to finish",
                )
                _store_calibration_failure(runtime, key, fingerprint, failure)
                _audit_calibration_result(
                    runtime,
                    command,
                    actor,
                    request_id,
                    failure,
                )
                raise failure
            try:
                yield
            finally:
                mutation_lock.release()

        def execute() -> dict[str, Any]:
            reservation = runtime.repository.reserve_remote_command(
                request_id=request_id,
                idempotency_key=key,
                fingerprint=fingerprint,
                command=command,
            )
            outcome = str(reservation["reservation_outcome"])
            if outcome == "CONFLICT":
                raise ApiFailure(
                    409,
                    "idempotency_conflict",
                    "Idempotency key was already used with a different command",
                )
            if outcome == "COMPLETED":
                if reservation["error_code"]:
                    raise _cached_calibration_failure(
                        reservation["error_code"],
                        reservation["error_message"],
                    )
                return dict(reservation["response"])
            if outcome == "PENDING":
                raise ApiFailure(
                    409,
                    "calibration_command_pending",
                    "Calibration command is already in progress",
                )
            if outcome == "RECONCILIATION_REQUIRED":
                raise ApiFailure(
                    409,
                    "calibration_reconciliation_required",
                    "Calibration command outcome requires operator reconciliation",
                )
            if outcome != "RESERVED":
                raise ApiFailure(
                    409,
                    "calibration_command_unavailable",
                    "Calibration command cannot be executed",
                )

            if before_lock is not None:
                before_lock()
            with bounded_mutation_lock():
                runtime.repository.audit(
                    "REMOTE_COMMAND_REQUESTED",
                    "gate",
                    runtime.config.gate_id,
                    actor,
                    {
                        "command": command,
                        "request_id": request_id,
                        "calibration_id": payload.get("calibration_id", ""),
                    },
                )
                try:
                    result = operation(actor)
                except KeyError as exc:
                    failure = ApiFailure(
                        404, "calibration_not_found", "Calibration not found"
                    )
                    _store_calibration_failure(
                        runtime, key, fingerprint, failure
                    )
                    _audit_calibration_result(
                        runtime, command, actor, request_id, failure
                    )
                    raise failure from exc
                except ValueError as exc:
                    failure = ApiFailure(422, "invalid_calibration", str(exc))
                    _store_calibration_failure(
                        runtime, key, fingerprint, failure
                    )
                    _audit_calibration_result(
                        runtime, command, actor, request_id, failure
                    )
                    raise failure from exc
                except ProtectedStateError as exc:
                    failure = ApiFailure(
                        503,
                        "calibration_unavailable",
                        "Calibration state is unavailable",
                    )
                    _store_calibration_failure(
                        runtime, key, fingerprint, failure
                    )
                    _audit_calibration_result(
                        runtime, command, actor, request_id, failure
                    )
                    raise failure from exc
                except TimeoutError as exc:
                    failure = ApiFailure(
                        504, "calibration_capture_timeout", str(exc)
                    )
                    _store_calibration_failure(
                        runtime, key, fingerprint, failure
                    )
                    _audit_calibration_result(
                        runtime, command, actor, request_id, failure
                    )
                    raise failure from exc
                except RuntimeError as exc:
                    message = str(exc)
                    unavailable = any(
                        marker in message.lower()
                        for marker in (
                            "not ready",
                            "disconnected",
                            "unavailable",
                        )
                    )
                    failure = ApiFailure(
                        503 if unavailable else 409,
                        (
                            "calibration_capture_unavailable"
                            if unavailable
                            else "calibration_conflict"
                        ),
                        message,
                    )
                    _store_calibration_failure(
                        runtime, key, fingerprint, failure
                    )
                    _audit_calibration_result(
                        runtime, command, actor, request_id, failure
                    )
                    raise failure from exc
                except Exception as exc:
                    runtime.repository.mark_remote_command_reconciliation_required(
                        idempotency_key=key,
                        fingerprint=fingerprint,
                        message=(
                            "Calibration operation ended without a known outcome: "
                            f"{type(exc).__name__}"
                        ),
                    )
                    _audit_calibration_result(
                        runtime,
                        command,
                        actor,
                        request_id,
                        ApiFailure(
                            500,
                            "calibration_reconciliation_required",
                            "Calibration outcome is unknown",
                        ),
                    )
                    raise

                response = _minimal_calibration_response(result)
                runtime.repository.complete_remote_command(
                    idempotency_key=key,
                    fingerprint=fingerprint,
                    response=response,
                )
                _audit_calibration_result(
                    runtime, command, actor, request_id, None
                )
                return response

        return await run_in_threadpool(execute)

    @app.post(
        "/api/v1/calibration/runs",
        status_code=201,
        dependencies=[Depends(authenticate)],
        response_model=CalibrationMutationResponse,
    )
    async def start_calibration(
        request: Request,
        body: StartCalibrationRequest | None = None,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        payload = _dump(body) if body is not None else {"notes": ""}
        return await execute_calibration_mutation(
            request,
            "calibration.start",
            payload,
            headers,
            lambda actor: runtime.calibration.start(actor, payload["notes"]),
        )

    @app.post(
        "/api/v1/calibration/runs/{calibration_id}/background",
        dependencies=[Depends(authenticate)],
        response_model=CalibrationMutationResponse,
    )
    async def submit_calibration_background(
        calibration_id: str,
        request: Request,
        body: CalibrationBackgroundRequest,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        payload = {"calibration_id": calibration_id, **_dump(body)}
        return await execute_calibration_mutation(
            request,
            "calibration.background",
            payload,
            headers,
            lambda actor: engine.capture_calibration_background(
                calibration_id,
                duration_seconds=body.duration_seconds,
                actor=actor,
            ),
        )

    @app.post(
        "/api/v1/calibration/runs/{calibration_id}/passes",
        dependencies=[Depends(authenticate)],
        response_model=CalibrationMutationResponse,
    )
    async def submit_calibration_pass(
        calibration_id: str,
        request: Request,
        body: CalibrationPassRequest,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        payload = {"calibration_id": calibration_id, **_dump(body)}
        return await execute_calibration_mutation(
            request,
            "calibration.pass",
            payload,
            headers,
            lambda actor: engine.capture_calibration_pass(
                calibration_id,
                direction=body.direction,
                expected_epcs=body.expected_epcs,
                timeout_seconds=body.timeout_seconds,
                actor=actor,
            ),
        )

    @app.post(
        "/api/v1/calibration/runs/{calibration_id}/evaluate",
        dependencies=[Depends(authenticate)],
        response_model=CalibrationMutationResponse,
    )
    async def evaluate_calibration(
        calibration_id: str,
        request: Request,
        body: EvaluateCalibrationRequest | None = None,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        payload = {"calibration_id": calibration_id}
        if body is not None:
            payload.update(_dump(body))
        return await execute_calibration_mutation(
            request,
            "calibration.evaluate",
            payload,
            headers,
            lambda actor: runtime.calibration.evaluate(calibration_id, actor),
        )

    @app.post(
        "/api/v1/calibration/runs/{calibration_id}/abort",
        dependencies=[Depends(authenticate)],
        response_model=CalibrationMutationResponse,
    )
    async def abort_calibration(
        calibration_id: str,
        request: Request,
        body: AbortCalibrationRequest,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        payload = {"calibration_id": calibration_id, **_dump(body)}
        return await execute_calibration_mutation(
            request,
            "calibration.abort",
            payload,
            headers,
            lambda actor: runtime.calibration.abort(
                calibration_id, actor, body.reason
            ),
            before_lock=lambda: engine.request_cancel_calibration_capture(
                f"Calibration capture cancelled: {body.reason}",
                calibration_id=calibration_id,
            ),
        )

    @app.post(
        "/api/v1/commands/start-inventory",
        dependencies=[Depends(authenticate)],
        response_model=CommandResponse,
    )
    async def start_inventory(
        request: Request,
        body: StartInventoryRequest,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        return await execute_command(
            request, "start-inventory", _dump(body), headers
        )

    @app.post(
        "/api/v1/commands/stop-inventory",
        dependencies=[Depends(authenticate)],
        response_model=CommandResponse,
    )
    async def stop_inventory(
        request: Request,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        await require_empty_body(request)
        return await execute_command(request, "stop-inventory", {}, headers)

    @app.post(
        "/api/v1/commands/commit-transaction",
        dependencies=[Depends(authenticate)],
        response_model=CommandResponse,
    )
    async def commit_transaction(
        request: Request,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        await require_empty_body(request)
        return await execute_command(request, "commit-transaction", {}, headers)

    @app.post(
        "/api/v1/commands/cancel-transaction",
        dependencies=[Depends(authenticate)],
        response_model=CommandResponse,
    )
    async def cancel_transaction(
        request: Request,
        body: CancelTransactionRequest,
        headers: tuple[str, str] = Depends(command_headers),
    ):
        return await execute_command(
            request, "cancel-transaction", _dump(body), headers
        )

    return app


def _error(status: int, code: str, message: str, request_id: str) -> JSONResponse:
    response = JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
    )
    # Self-contained so early middleware returns and ServerErrorMiddleware
    # responses carry the same headers as every other response.
    return _finalize_headers(response, request_id)


def _map_command_failure(exc: RemoteCommandFailure) -> ApiFailure:
    if exc.code == "command_timeout":
        return ApiFailure(504, exc.code, exc.message)
    if exc.code in {
        "command_reconciliation_required",
        "idempotency_conflict",
        "command_rejected",
    }:
        message = exc.message.lower()
        if any(
            marker in message
            for marker in ("not ready", "not connected", "model", "calibration")
        ):
            return ApiFailure(503, "reader_not_ready", exc.message)
        return ApiFailure(409, exc.code, exc.message)
    if exc.code == "calibration_required":
        return ApiFailure(503, exc.code, exc.message)
    if exc.code == "command_not_found":
        return ApiFailure(404, exc.code, exc.message)
    return ApiFailure(422, exc.code, exc.message)


def _cached_calibration_failure(code: str, message: str) -> ApiFailure:
    status_by_code = {
        "calibration_not_found": 404,
        "calibration_conflict": 409,
        "calibration_unavailable": 503,
        "calibration_capture_unavailable": 503,
        "calibration_capture_timeout": 504,
        "calibration_command_timeout": 504,
        "invalid_calibration": 422,
    }
    return ApiFailure(status_by_code.get(code, 422), code, message)


def _minimal_calibration_response(result: dict[str, Any]) -> dict[str, str]:
    """Keep raw/derived commissioning evidence out of idempotency storage."""

    return {
        "calibration_id": str(result.get("calibration_id") or ""),
        "status": str(result.get("status") or ""),
        "updated_at": str(result.get("updated_at") or ""),
    }


def _store_calibration_failure(
    runtime,
    idempotency_key: str,
    fingerprint: str,
    failure: ApiFailure,
) -> None:
    runtime.repository.complete_remote_command(
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        response={},
        error_code=failure.code,
        error_message=failure.message,
    )


def _audit_calibration_result(
    runtime,
    command: str,
    actor: str,
    request_id: str,
    failure: ApiFailure | None,
) -> None:
    runtime.repository.audit(
        "REMOTE_COMMAND_FAILED" if failure is not None else "REMOTE_COMMAND_SUCCEEDED",
        "gate",
        runtime.config.gate_id,
        actor,
        {
            "command": command,
            "request_id": request_id,
            "error": failure.message[:500] if failure is not None else "",
        },
    )
