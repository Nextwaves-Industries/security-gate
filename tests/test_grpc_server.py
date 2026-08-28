from __future__ import annotations

import asyncio
from types import SimpleNamespace

import grpc
import pytest

from conftest import FakeControl
from gate_service.api import grpc_server
from gate_service.proto import gate_stream_pb2


class Aborted(Exception):
    pass


class FakeContext:
    def __init__(self, token: str | None = "test-token"):
        self._metadata = []
        if token is not None:
            self._metadata.append(SimpleNamespace(key="authorization", value=f"Bearer {token}"))
        self.aborted: tuple[grpc.StatusCode, str] | None = None

    def invocation_metadata(self):
        return self._metadata

    async def abort(self, code, details):
        self.aborted = (code, details)
        raise Aborted()


class FakeSubscription:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.overflowed = False


class FakeEventBus:
    def __init__(self):
        self.subscription = FakeSubscription()
        self.unsubscribed = False

    def subscribe(self, event_type, *, max_events):
        self.max_events = max_events
        return self.subscription

    def unsubscribe(self, subscription):
        self.unsubscribed = True


def _service(control=None, bus=None):
    return grpc_server.GateStreamService(control or FakeControl(), bus or FakeEventBus(), "test-token")


def _run(coro):
    return asyncio.run(coro)


def test_get_status_ok():
    result = _run(_service().GetStatus(gate_stream_pb2.GetStatusRequest(), FakeContext()))
    assert result.gate_id == "G1" and result.ready is True


def test_get_status_requires_bearer():
    ctx = FakeContext(token=None)
    with pytest.raises(Aborted):
        _run(_service().GetStatus(gate_stream_pb2.GetStatusRequest(), ctx))
    assert ctx.aborted[0] == grpc.StatusCode.UNAUTHENTICATED


def test_get_status_timeout_maps_to_deadline_exceeded():
    control = FakeControl()
    control.raise_exc = TimeoutError()
    ctx = FakeContext()
    with pytest.raises(Aborted):
        _run(_service(control).GetStatus(gate_stream_pb2.GetStatusRequest(), ctx))
    assert ctx.aborted[0] == grpc.StatusCode.DEADLINE_EXCEEDED


def test_get_status_generic_exception_does_not_leak():
    control = FakeControl()
    control.raise_exc = ValueError("internal secret")
    ctx = FakeContext()
    with pytest.raises(Aborted):
        _run(_service(control).GetStatus(gate_stream_pb2.GetStatusRequest(), ctx))
    code, details = ctx.aborted
    assert code == grpc.StatusCode.INTERNAL
    assert "internal secret" not in details


def _envelope(occurred_at: str):
    return SimpleNamespace(
        schema_version="1",
        event_id="e1",
        event_type="gate.state.changed",
        occurred_at=occurred_at,
        gate_id="G1",
        correlation_id="c1",
        data={"state": "READY"},
    )


def test_watch_events_survives_bad_timestamp_and_reports_overflow():
    bus = FakeEventBus()

    async def scenario():
        service = _service(bus=bus)
        ctx = FakeContext()
        stream = service.WatchEvents(SimpleNamespace(event_type=["gate.state.changed"]), ctx)
        await bus.subscription.queue.put(_envelope("not-a-timestamp"))
        first = await stream.__anext__()
        assert first.event_id == "e1"
        bus.subscription.overflowed = True
        await bus.subscription.queue.put(None)
        with pytest.raises(Aborted):
            await stream.__anext__()
        return ctx

    ctx = _run(scenario())
    assert ctx.aborted[0] == grpc.StatusCode.RESOURCE_EXHAUSTED
    assert bus.unsubscribed is True
    assert bus.max_events == grpc_server._WATCH_EVENTS_QUEUE_DEPTH


def test_watch_events_clean_close():
    bus = FakeEventBus()

    async def scenario():
        stream = _service(bus=bus).WatchEvents(SimpleNamespace(event_type=[]), FakeContext())
        await bus.subscription.queue.put(None)
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()

    _run(scenario())
    assert bus.unsubscribed is True
