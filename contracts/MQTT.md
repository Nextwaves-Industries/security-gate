# MQTT 5 delivery contract

This document defines the customer-facing MQTT contract for one physical gate.
The broker is external to the container and must accept MQTT 5 over TLS. The
supported topic root is:

```text
rfid/portal/v1/{gate_id}
```

`gate_id` is the validated `GATE_ID` configured for that container. Each gate
must use a unique MQTT client ID; leaving `MQTT_CLIENT_ID` empty derives
`nextwaves-{gate_id}`.

## Published topics

| Topic | QoS | Retained | Delivery role |
|---|---:|---:|---|
| `rfid/portal/v1/{gate_id}/state` | 1 | yes | Latest connection and gate-state snapshot |
| `rfid/portal/v1/{gate_id}/events/{event_type}` | 1 | no | Business events and live operational events |

All payloads are UTF-8 JSON. State and event payloads use the envelope defined
by [event-envelope.schema.json](mqtt/event-envelope.schema.json):

```text
schema_version, event_id, event_type, occurred_at,
gate_id, correlation_id, data
```

Consumers may subscribe to the concrete state topic and to
`rfid/portal/v1/{gate_id}/events/#`. Publishers never use wildcard topics.

## State and Last Will

The state message is QoS 1 and retained, so a new subscriber receives the most
recent snapshot. After connecting, the service publishes an `online` state and
refreshes it periodically. Live state transitions are also emitted as
non-retained `gate.state.changed` events, so the retained snapshot may lag a
transition until the next heartbeat.

The MQTT Last Will is published to the same state topic with QoS 1 and retained
delivery. Its envelope has `event_type: gate.state.changed` and
`data.connection: offline`. A clean shutdown explicitly publishes the same
offline state before disconnecting. Therefore the retained state represents
the latest known connection state; it is not an append-only event history.

## Durable business delivery

Business events whose type begins with `inventory.` are persisted in the same
SQLite transaction as the corresponding domain change. Pending rows are sent
from the transactional outbox to:

```text
rfid/portal/v1/{gate_id}/events/{event_type}
```

The service publishes them with QoS 1 and `retain=false`. It marks an outbox row
`DELIVERED` only after the MQTT client reports publish completion; at QoS 1 this
requires the broker PUBACK. A timeout, disconnect or publish error leaves the
row pending/failed for retry after reconnect.

This is at-least-once delivery. A process failure after PUBACK but before the
SQLite `DELIVERED` update can publish the same event again. The stored
`event_id` remains stable across retries, and consumers must use `event_id` as
their deduplication key. Message arrival order must not replace reconciliation
against the REST transaction API.

## Live operational events

Operational events such as `gate.service.started`, `gate.device.*`,
`gate.state.changed` and `gate.service.offline` use the same non-retained event
topic shape and QoS 1, but their in-process queue is not a transactional outbox.
They are best-effort live observability and may be absent across a disconnect or
process crash. Customers must not treat these `gate.*` messages as durable
business records.

## Remote commands are disabled

The production container forces `allow_remote_commands=false`. It does not
subscribe to `rfid/portal/v1/{gate_id}/commands/+`, and MQTT command/response
topics are not a supported command plane. The bundled response schema is a
compatibility artifact and does not enable remote commands. Use authenticated
REST/HTTPS for every command and calibration mutation.

## Customer acceptance

For each commissioned gate, verify:

1. a new subscriber receives the retained state snapshot;
2. an unclean disconnect causes the broker to retain the offline Last Will;
3. event messages are QoS 1 and not retained;
4. an MQTT outage leaves business events in the SQLite outbox;
5. reconnect sends pending events and duplicates are removed by `event_id`;
6. publishing to a command topic cannot execute a gate command.
