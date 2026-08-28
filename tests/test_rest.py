from __future__ import annotations

import threading

import pytest

from conftest import FakeControl, FakeEngine, FakeRepository, FakeRuntime

REQUIRED_HEADERS = {"X-Request-ID", "Cache-Control", "X-Content-Type-Options"}


def _assert_envelope(response, status: int, code: str | None = None):
    assert response.status_code == status, response.text
    for name in REQUIRED_HEADERS:
        assert name in response.headers, f"{name} missing on {status}"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    if code:
        assert body["error"]["code"] == code


# --------------------------------------------------------------------------
# Headers / error envelope
# --------------------------------------------------------------------------
def test_unauthorized_has_headers(make_app):
    _, client, *_ = make_app()
    _assert_envelope(client.get("/api/v1/status"), 401, "unauthorized")


def test_not_found_has_headers(make_app, auth):
    _, client, *_ = make_app()
    _assert_envelope(client.get("/api/v1/transactions/nope", headers=auth), 404, "transaction_not_found")


def test_early_413_has_headers(make_app, command_headers):
    _, client, *_ = make_app(max_body_bytes=16)
    response = client.post(
        "/api/v1/commands/start-inventory",
        headers={**command_headers, "Content-Length": "9999"},
        content=b"",
    )
    _assert_envelope(response, 413, "request_too_large")


def test_invalid_content_length_has_headers(make_app, command_headers):
    _, client, *_ = make_app()
    response = client.post(
        "/api/v1/commands/stop-inventory",
        headers={**command_headers, "Content-Length": "abc"},
    )
    _assert_envelope(response, 400, "invalid_content_length")


def test_validation_422_has_headers(make_app, command_headers):
    _, client, *_ = make_app()
    response = client.post(
        "/api/v1/commands/start-inventory", headers=command_headers, json={"reference": ""}
    )
    _assert_envelope(response, 422, "invalid_payload")


def test_invalid_json_400(make_app, command_headers):
    _, client, *_ = make_app()
    response = client.post(
        "/api/v1/commands/start-inventory",
        headers={**command_headers, "Content-Type": "application/json"},
        content=b"{not json",
    )
    _assert_envelope(response, 400, "invalid_json")


def test_unhandled_500_has_headers_and_no_leak(make_app, auth):
    repo = FakeRepository()
    repo.fail_with = RuntimeError("secret internal detail")
    _, client, *_ = make_app(runtime=FakeRuntime(repo))
    response = client.get("/api/v1/transactions", headers=auth)
    _assert_envelope(response, 500, "internal_error")
    assert "secret internal detail" not in response.text


def test_request_id_echo_and_sanitise(make_app, auth):
    _, client, *_ = make_app()
    ok = client.get("/api/v1/status", headers={**auth, "X-Request-ID": "abc-123"})
    assert ok.headers["X-Request-ID"] == "abc-123"
    bad = client.get("/api/v1/status", headers={**auth, "X-Request-ID": "bad id!"})
    assert bad.headers["X-Request-ID"].startswith("req_")


def test_success_has_headers(make_app, auth):
    _, client, *_ = make_app()
    response = client.get("/api/v1/status", headers=auth)
    assert response.status_code == 200
    assert {name.lower() for name in REQUIRED_HEADERS} <= {name.lower() for name in response.headers}


# --------------------------------------------------------------------------
# Body cap
# --------------------------------------------------------------------------
def test_chunked_body_over_cap_is_rejected(make_app, command_headers):
    _, client, runtime, *_ = make_app(max_body_bytes=16)

    def chunks():
        yield b'{"reason": "'
        yield b"x" * 64
        yield b'"}'

    response = client.post(
        "/api/v1/commands/cancel-transaction",
        headers={**command_headers, "Content-Type": "application/json"},
        content=chunks(),
    )
    _assert_envelope(response, 413, "request_too_large")


def test_body_under_cap_reaches_handler(make_app, command_headers):
    _, client, *_ = make_app(max_body_bytes=4096)
    response = client.post(
        "/api/v1/commands/cancel-transaction",
        headers=command_headers,
        json={"reason": "operator abort"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True


# --------------------------------------------------------------------------
# Composition-root guarantees
# --------------------------------------------------------------------------
def test_missing_remote_command_lock_fails_fast():
    from conftest import FakeSettings
    from gate_service.api.rest import create_app

    with pytest.raises(AttributeError):
        create_app(FakeRuntime(with_lock=False), FakeControl(), FakeEngine(), FakeSettings())


# --------------------------------------------------------------------------
# OpenAPI exposure
# --------------------------------------------------------------------------
def test_openapi_requires_auth_in_production(make_app, auth):
    _, client, *_ = make_app(development=False)
    _assert_envelope(client.get("/openapi.json"), 401, "unauthorized")
    assert client.get("/openapi.json", headers=auth).status_code == 200
    assert client.get("/docs").status_code == 404


def test_openapi_public_in_development(make_app):
    _, client, *_ = make_app(development=True)
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


# --------------------------------------------------------------------------
# Blocking work stays off the event loop
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path,repo_call",
    [
        ("/api/v1/transactions", "list_transactions"),
        ("/api/v1/transactions/tx-1", "transaction_reconciliation"),
        ("/api/v1/transactions/tx-1/tags", "net_transaction_tags"),
        ("/api/v1/transactions/tx-1/passages", "list_passages"),
        ("/api/v1/transactions/tx-1/audit", "list_transaction_audit"),
    ],
)
def test_transaction_reads_run_in_threadpool(make_app, auth, path, repo_call):
    app, client, runtime, *_ = make_app()
    loop_thread: dict[str, int] = {}

    @app.middleware("http")
    async def capture(request, call_next):
        loop_thread["id"] = threading.get_ident()
        return await call_next(request)

    response = client.get(path, headers=auth)
    assert response.status_code == 200, response.text
    assert runtime.repository.threads[repo_call] != loop_thread["id"]
    if path != "/api/v1/transactions":
        assert runtime.repository.threads["get_transaction_record"] != loop_thread["id"]


def test_readyz_runs_engine_status_off_loop(make_app):
    engine = FakeEngine(ready=False)
    app, client, *_ = make_app(engine=engine)
    loop_thread: dict[str, int] = {}

    @app.middleware("http")
    async def capture(request, call_next):
        loop_thread["id"] = threading.get_ident()
        return await call_next(request)

    response = client.get("/readyz")
    _assert_envelope(response, 503, "calibration_required")
    assert engine.thread != loop_thread["id"]


def test_transactions_status_filter(make_app, auth):
    _, client, runtime, *_ = make_app()
    assert client.get("/api/v1/transactions?status=committed", headers=auth).status_code == 200
    name, args, kwargs = runtime.repository.calls[-1]
    assert kwargs["status"] == "COMMITTED"
    _assert_envelope(client.get("/api/v1/transactions?status=bogus", headers=auth), 422, "invalid_status")


def test_audit_payload_decoding(make_app, auth):
    _, client, *_ = make_app()
    items = client.get("/api/v1/transactions/tx-1/audit", headers=auth).json()["items"]
    assert items[0]["payload"] == {"a": 1}
    assert items[1]["payload"] == {}
    assert "payload_json" not in items[0]


# --------------------------------------------------------------------------
# Calibration mutation stores minimal response only
# --------------------------------------------------------------------------
def test_calibration_start_stores_minimal_response(make_app, command_headers):
    _, client, runtime, *_ = make_app()
    response = client.post("/api/v1/calibration/runs", headers=command_headers, json={"notes": "n"})
    assert response.status_code == 201, response.text
    stored = runtime.repository.commands["key-1"]["response"]
    assert set(stored) == {"calibration_id", "status", "updated_at"}
    assert runtime.repository.audits[-1][0] == "REMOTE_COMMAND_SUCCEEDED"


# --------------------------------------------------------------------------
# Contract guard
# --------------------------------------------------------------------------
def test_routes_match_bundled_contract(make_app, contract_openapi):
    app, *_ = make_app(development=True)
    live = app.openapi()["paths"]
    expected = contract_openapi["paths"]
    assert set(live) == set(expected)
    for path, ops in expected.items():
        assert set(live[path]) == set(ops), path
        for method, op in ops.items():
            assert set(live[path][method].get("responses", {})) == set(op.get("responses", {})), (path, method)
