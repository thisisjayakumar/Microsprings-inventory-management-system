# ADR 0002 — One stock ledger and one writer

- **Status:** Accepted
- **Date:** 2026-10-28
- **Supersedes:** none

## Context

Stock is currently mutated in at least 24 places, with 19 direct
`InventoryTransaction` creates, 33 `get_or_create` balance lookups, and 35
balance saves. Two projections exist for raw material (`RMStockBalance` and
`RMStockBalanceHeat`), and they disagree after MO approval and stop. The heat
projection is overwritten by `RMStockBalanceHeat.update_from_heat_numbers()`,
which recomputes gross on-hand and erases reservations.

There is no row locking anywhere in the codebase, so concurrent approvals,
receipts, and dispatches can interleave. There is no replay guard: the existing
`InventoryTransaction.idempotency_key` is not unique and is unused.

## Decision

### Authoritative quantities

| Quantity | Authoritative source |
|---|---|
| RM on-hand per lot | `HeatNumber.total_weight_kg - HeatNumber.consumed_quantity_kg` |
| RM on-hand per material | Sum of heat lot on-hand |
| RM reserved | `RawMaterialAllocation` rows in `reserved` or `locked` status |
| RM available | on-hand minus active reservations |
| RM at a location | `ProductLocation.quantity` |
| FG on-hand | `DispatchBatch.quantity_packed - quantity_dispatched` |
| FG reserved | `FGStockReservation` |

`HeatNumber.items` is descriptive metadata. `consumed_quantity_kg` is the
authoritative consumption counter.

### Projections

- `RMStockBalanceHeat.total_available_quantity_kg` means **net available**, not
  gross. This preserves the current API meaning of `available_quantity` and the
  behavior of stock checks and low-stock filters.
- `RMStockBalance.available_quantity` is the legacy projection and follows the
  same net-available meaning.
- `reserved_kg` is derived from allocations and is not stored on the balance row.
- `update_from_heat_numbers()` is replaced by `StockService.reconcile()`, which
  computes `on_hand - active_reserved` and writes the projection.

### Single writer

`inventory/application/stock_service.py` is the only module allowed to mutate
stock balances or create ledger rows. Viewsets, serializers, and models may call
the service but must not perform stock arithmetic. CI adds a check for direct
`RMStockBalance*` writes, `HeatNumber.consumed_quantity_kg` writes, and
`InventoryTransaction.objects.create` outside the service and migrations.

### Ledger

`InventoryTransaction` remains the ledger and is extended additively:

- `raw_material` (nullable FK) for material-level movements.
- `movement_type` with precise semantics: `receipt`, `reservation`, `release`,
  `consumption`, `production`, `transfer`, `scrap`, `adjustment`, `dispatch`,
  `return`.
- `operation_key` groups every row produced by one business operation.
- Unique, nullable `idempotency_key` guards each row.
- `metadata` JSON for heat breakdown, actor, reason, and `direction` for
  adjustments.
- Indexes on `(raw_material, transaction_datetime)`,
  `(reference_type, reference_id)`, and `(operation_key)`.
- `CheckConstraint(quantity > 0)`. Direction comes from `movement_type` and
  `location_from`/`location_to`; for adjustments it comes from
  `metadata.direction`.
- `quantity` widens from 2 to 3 decimal places to match the heat, material, and
  location models.

`transaction_type` stays for backward compatibility. New code sets both the
closest legacy value and the precise `movement_type`.

**Granularity.** One row per movement × material × heat × location. Each row
keeps its existing 1:1 `InventoryTransactionHeat` detail. Multi-line operations
produce several rows sharing one `operation_key`, each with its own
`idempotency_key`.

### Reservation and consumption

- `reserve` reduces available and creates a reservation movement.
- `lock_allocation()` changes status and timestamps only; it must not decrement
  stock again.
- `release` restores available once and creates a release movement.
- `consume` increments `consumed_quantity_kg` and creates a consumption movement.
  If the quantity was already reserved, it does not reduce available a second
  time (`from_reservation=True`).
- `receive`, `transfer`, `scrap`, `dispatch`, and `adjustment` follow the same
  explicit pattern.

### Concurrency

Every stock-changing use case runs in `transaction.atomic()` and:

1. Locks `RawMaterial.objects.select_for_update().get(pk=...)` as the aggregate
   root. This serializes stock changes for one material.
2. Locks balance rows (`RMStockBalanceHeat`, then `RMStockBalance`) when they
   must be updated.
3. Validates positivity and availability.
4. Writes balances with `F()` expressions and refreshes before returning.
5. Writes the ledger row with a deterministic key.
6. Commits.

Multi-material operations lock material IDs in ascending order. A bounded retry
(3 attempts) handles MySQL deadlock error 1213.

### Reconciliation

A `reconcile_stock_balances` management command reports drift with `--dry-run`
and, without it, corrects projections in a transaction and records an adjustment
ledger row per correction. It is idempotent and safe to re-run.

## Consequences

**Positive**

- One meaning for available stock across heat, legacy, API, and reports.
- Reservations survive reconciliation and cannot be erased by a gross recompute.
- Concurrent stock changes serialize on the material row instead of racing.
- Replays are detectable at both the operation and row level.

**Negative**

- The service becomes a central, high-traffic module and must stay small and
  well tested.
- The legacy projection is kept consistent through Phase 1 and removed only
  after a reconciliation period and an API audit, so every write must update both
  projections consistently.
- The `quantity > 0` constraint requires a pre-flight check for existing
  non-positive rows; those rows are never rewritten automatically.

## Alternatives considered

**Keep both projections independent.** Rejected. It is the current bug.

**Store reserved quantity on the balance row.** Rejected. Allocations are
already the source of truth and a stored copy would drift.

**Use a separate ledger table.** Rejected. `InventoryTransaction` already has
the references, audit fields, and heat detail that reports and the UI use.

**Optimistic concurrency instead of row locks.** Rejected for stock. Retry logic
would leak into every caller; locking the material row is simpler and matches
the write volume.
