"""TLS gRPC status and non-durable live event stream."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hmac
import json
import logging
from pathlib import Path
from typing import Any

import grpc
from google.protobuf.json_format import ParseDict
from google.protobuf.struct_pb2 import Struct
from google.protobuf.timestamp_pb2 import Timestamp

from gate_service.proto import gate_stream_pb2, gate_stream_pb2_grpc


log = logging.getLogger("gate_service.grpc")

# GateServiceSettings does not expose transport tunables; keep them as named
# constants rather than literals scattered through the server code.
_WATCH_EVENTS_QUEUE_DEPTH = 1000  # events buffered per slow client before abort
_KEEPALIVE_TIME_MS = 30_000
_KEEPALIVE_TIMEOUT_MS = 10_000


def _timestamp(value: str | None = None) -> Timestamp:
    result = Timestamp()
    parsed: datetime | None = None
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            # One malformed occurred_at must not terminate a live stream.
            log.warning("Malformed event timestamp %r; using current time", value)
    if parsed is None:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    result.FromDatetime(parsed.astimezone(timezone.utc))
    return result


def _struct(value: dict[str, Any]) -> Struct:
    # Status can contain enums/datetimes from existing domain services. A JSON
    # normalization boundary gives protobuf.Struct a stable primitive tree.
    normalized = json.loads(json.dumps(value, default=str))
    result = Struct()
    ParseDict(normalized, result)
    return result


class GateStreamService(gate_stream_pb2_grpc.GateStreamServiceServicer):
    def __init__(self, control, event_bus, token: str, *, timeout_s: float = 10.0):
        self.control = control
        self.event_bus = event_bus
        self.token = token
        self.timeout_s = timeout_s

    async def _authenticate(self, context: grpc.aio.ServicerContext) -> None:
        metadata = {item.key.lower(): item.value for item in context.invocation_metadata()}
        scheme, separator, supplied = metadata.get("authorization", "").partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not hmac.compare_digest(supplied.strip(), self.token)
        ):
            # context.abort() raises grpc.aio.AbortError; it never returns.
            await context.abort(
                grpc.StatusCode.UNAUTHENTICATED, "Valid bearer token required"
            )

    async def GetStatus(self, request, context):
        await self._authenticate(context)
        try:
            status = await asyncio.to_thread(self.control.status, self.timeout_s)
            return gate_stream_pb2.GateStatus(
                gate_id=str(status.get("gate_id", "")),
                state=str(status.get("state", "")),
                ready=bool(status.get("ready", False)),
                observed_at=_timestamp(),
                details=_struct(status),
            )
        except TimeoutError:
            await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "Status timed out")
        except RuntimeError as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))
        except grpc.aio.AbortError:
            raise
        except Exception:
            # Never leak internal exception text to the client.
            log.exception("GetStatus failed")
            await context.abort(grpc.StatusCode.INTERNAL, "Unexpected server error")

    async def WatchEvents(self, request, context):
        await self._authenticate(context)
        subscription = self.event_bus.subscribe(
            request.event_type, max_events=_WATCH_EVENTS_QUEUE_DEPTH
        )
        try:
            while True:
                envelope = await subscription.queue.get()
                if envelope is None:
                    if subscription.overflowed:
                        await context.abort(
                            grpc.StatusCode.RESOURCE_EXHAUSTED,
                            "Client is too slow; resynchronize through REST",
                        )
                    return
                yield gate_stream_pb2.GateEvent(
                    schema_version=envelope.schema_version,
                    event_id=envelope.event_id,
                    event_type=envelope.event_type,
                    occurred_at=_timestamp(envelope.occurred_at),
                    gate_id=envelope.gate_id,
                    correlation_id=envelope.correlation_id,
                    data=_struct(envelope.data),
                )
        finally:
            self.event_bus.unsubscribe(subscription)


async def start_grpc_server(control, event_bus, settings) -> grpc.aio.Server:
    server = grpc.aio.server(
        options=(
            ("grpc.max_receive_message_length", settings.max_body_bytes),
            ("grpc.max_send_message_length", settings.max_body_bytes),
            ("grpc.keepalive_time_ms", _KEEPALIVE_TIME_MS),
            ("grpc.keepalive_timeout_ms", _KEEPALIVE_TIMEOUT_MS),
            # WatchEvents streams can idle for long periods; keep probing.
            ("grpc.keepalive_permit_without_calls", 1),
        )
    )
    gate_stream_pb2_grpc.add_GateStreamServiceServicer_to_server(
        GateStreamService(
            control,
            event_bus,
            settings.api_token(),
            timeout_s=settings.command_timeout_s,
        ),
        server,
    )
    address = f"{settings.grpc_host}:{settings.grpc_port}"
    if settings.allow_insecure:
        bound_port = server.add_insecure_port(address)
    else:
        certificate = Path(settings.tls_cert_file).read_bytes()
        private_key = Path(settings.tls_key_file).read_bytes()
        credentials = grpc.ssl_server_credentials(((private_key, certificate),))
        bound_port = server.add_secure_port(address, credentials)
    if bound_port == 0:
        raise RuntimeError(f"gRPC server could not bind {address}")
    try:
        await server.start()
    except BaseException:
        # add_*_port may succeed before the async server startup fails. Do not
        # leave that partially initialized server alive while the application
        # composition root rolls back its other resources.
        with suppress(Exception):
            await server.stop(grace=0)
        raise
    return server
