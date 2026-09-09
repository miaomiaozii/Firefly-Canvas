# Photoshop round-trip protocol v1

This protocol is the observable boundary between the core canvas and the
Firefly Canvas PS connector. The connector only performs “send to Photoshop”
and “return from Photoshop”; generation, Agent, ComfyUI, and asset management
are not part of this surface.

## Transport and trust boundary

- The first phase listens only on `127.0.0.1`. Loopback connections do not use
  authentication or pairing.
- Lack of authentication does not weaken request binding: every operation must
  carry the exact `project_id`, `source_node_id`, and `request_id` issued by the
  canvas service.
- The canvas service chooses all project paths, media IDs, node IDs, and edge
  IDs. A connector-supplied name is display metadata only and can never select
  a filesystem path.
- Returned media carries a SHA-256. Base64 may be used as a transport encoding;
  it is never stored in canvas JSON.

## Request

The canvas creates a round trip and sends this envelope to the connector:

```json
{
  "project_id": "project-id",
  "source_node_id": "source-node-id",
  "request_id": "unique-one-time-id",
  "source_media_id": "project-media-id",
  "created_at": 1700000000,
  "expires_at": 1700000300
}
```

The core canvas sends this envelope to connected PS connectors over the local
`/ws/stats?client_id=ps_*` channel.  The connector may also create a binding
from its explicit fallback form when no canvas UI is available; it never
chooses project paths or media IDs.

The service stores the same binding before exposing source bytes. A request ID
is globally unique for the running project service and cannot be reused.

## Return

The connector returns `project_id`, `source_node_id`, and `request_id` unchanged,
plus a plain display name, image MIME type, SHA-256, and the encoded media bytes. Whether those
bytes came from the current layer, selection, or composite is connector task
metadata, not part of the validated v1 binding.

The service validates in this order before mutating project state:

1. the request exists and has not already returned;
2. project and source identifiers exactly match the stored request;
3. current time is strictly before `expires_at`;
4. returned media is non-empty and its declared SHA-256 matches its bytes.

An accepted return stores a new project media original, creates a new
`ps_edit_result` node, adds one `derived_from` edge from the source node, and
marks the request `returned`. The source node and media are never overwritten.
Any failure in those four protocol checks leaves media, nodes, edges, and request
consumption unchanged. The prototype verified this final state and rejection
behaviour. Crash-safe atomic persistence and rollback were implemented in
production (issue #95): the request table is persisted per project as
`photoshop-round-trips.json` on every state transition (create / returned /
expired / pruned), and a startup reconciliation reloads the tables, expires
stale pendings, and repairs a `processing` request that crashed mid-commit
(backfilled to `returned` when the committed result node already carries the
request id, otherwise dropped back to `pending` so the connector can retry).
A restart therefore never loses an in-flight round trip: a later `return`
either completes normally or fails with an explicit `REQUEST_EXPIRED` /
`DUPLICATE_RETURN` / `REQUEST_NOT_FOUND`. Production also performs final
path-containment checks before mutating project state;
the prototype proved only that its server-selected successful paths stayed in
the project and connector-supplied traversal names were rejected earlier.

## States

| State | Meaning | Allowed transition |
| --- | --- | --- |
| `pending` | Binding exists and `now < expires_at` | `returned` |
| `returned` | One return was accepted | terminal |
| `expired` | Derived when a pending request reaches `expires_at` | terminal |

Expired requests may be removed after an implementation-defined retention
period, but their IDs must not be reused while replay could still occur.

## Stable rejection codes

| HTTP | Code | Meaning |
| ---: | --- | --- |
| 400 | `INVALID_IDENTIFIER` | A protocol identifier is malformed. |
| 400 | `INVALID_RETURN_PATH` | Connector metadata contains a path instead of a plain name. |
| 404 | `PROJECT_NOT_FOUND` | The requested project does not exist. |
| 404 | `SOURCE_NODE_NOT_FOUND` | The source is absent from the bound project. |
| 404 | `REQUEST_NOT_FOUND` | The request ID is unknown. |
| 409 | `REQUEST_CONFLICT` | A request ID was reused. |
| 409 | `PROJECT_MISMATCH` | Return project differs from the stored binding. |
| 409 | `SOURCE_NODE_MISMATCH` | Return source differs from the stored binding. |
| 409 | `DUPLICATE_RETURN` | The one-time request already accepted a result. |
| 410 | `REQUEST_EXPIRED` | The request reached its expiry before return. |
| 422 | `EMPTY_MEDIA` | No returned media bytes were supplied. |
| 422 | `MEDIA_HASH_MISMATCH` | Declared and computed SHA-256 differ. |
| 413 | `MEDIA_TOO_LARGE` | Source or returned image exceeds the PS 128MB limit. |

Concurrent requests are independent. Returns may arrive in any order; each
result is connected only to the source stored in its own binding.
