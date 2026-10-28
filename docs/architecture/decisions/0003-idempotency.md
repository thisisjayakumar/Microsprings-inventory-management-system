# ADR 0003 — Idempotent stock operations

- **Status:** Accepted
- **Date:** 2026-10-28
- **Supersedes:** none

## Context

Workflow endpoints can be retried by clients, double-clicked, or replayed after a
timeout. Today a retry can apply a stock movement twice: the `idempotency_key`
field on `InventoryTransaction` exists but is neither unique nor populated. There
is no operation-level replay guard, and multi-row operations have no shared
identifier.

## Decision

Use two complementary keys.

### `operation_key` — one per business operation

Every stock-changing use case receives a deterministic operation key:

```
f"{reference_type}:{reference_id}:{action}"
```

Examples:

- `mo:123:reserve`
- `batch:456:start`
- `grn:789:line:3:receive`
- `dispatch:101:confirm`
- `rm_return:55:disposition`

All ledger rows produced by the operation share it. Before doing work, the
service checks whether any row with that key exists. A **complete** row set means
the operation already ran and the service returns the existing result instead of
re-applying stock. A **partial** row set means prior corruption, not a replay:
the service raises and points at reconciliation. Because the whole operation runs
in one `transaction.atomic()`, either all rows committed or none did.

Partial receipts include the GRN line and heat number in the key so a second
delivery is a new operation, not a replay. Keys are derived from stable business
identifiers, never from a timestamp.

An optional `Idempotency-Key` request header, when present, replaces the default
operation key for client-driven retries.

### `idempotency_key` — one per ledger row

Each row gets a deterministic key derived from the operation and the row's
dimensions:

```python
def make_key(operation_key, material_id, heat_id=None, location_id=None):
    return f"{operation_key}:{material_id}:{heat_id or 0}:{location_id or 0}"[:64]
```

The column becomes unique and nullable (MySQL permits multiple `NULL`s). It is
the final database guard: if a concurrent request slips past the operation check,
the duplicate insert fails and the service converts that failure into the same
replay response.

## Replay behavior

1. Compute the operation key.
2. Query for an existing row with that `operation_key`.
3. If found, return the existing result. Do not touch balances.
4. Otherwise run the operation in one transaction, writing every row with its
   `idempotency_key`.
5. If a unique-constraint violation occurs on `idempotency_key`, treat it as a
   replay and return the existing result.

Replay must be side-effect free: no second ledger row, no second balance change,
no second notification.

## Consequences

**Positive**

- Retries and double submissions are safe for every stock-changing endpoint.
- Multi-row operations are auditable as a group through `operation_key`.
- Partial GRN deliveries remain distinct operations.

**Negative**

- Every use case must derive and pass a stable operation key.
- The key format is a compatibility surface; changing it changes replay
  detection. It is centralized in one helper and covered by tests.
- The unique index adds a small write cost, acceptable for the safety gained.

## Alternatives considered

**Client-supplied idempotency keys only.** Rejected. Internal workflows (approve,
stop, complete) also need protection and clients do not always send a key.

**Unique constraint on `(reference_type, reference_id, transaction_type)`.**
Rejected. Legitimate operations produce multiple rows with the same reference
and type (one per heat or location), and partial receipts reuse a reference.

**Advisory locks or a separate operations table.** Rejected for Phase 1. The
ledger already provides a durable record and a natural place for the guard.
