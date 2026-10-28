# Backend Architecture Refactor — Design

- **Status:** Proposed (awaiting approval)
- **Date:** 2026-10-28
- **Scope:** Django backend in `Microsprings-inventory-management-system`
- **Chosen approach:** A — backend-first modular monolith. The Next.js frontend is a follow-on track in its own repository (`../microsprings/msp-frontend`) and is not modified by this design.
- **Related audit:** findings summarized in §4; evidence gathered from the repository at commit `841bb1a`.

---

## 1. Intent and why

The system works, but its inventory and manufacturing rules are spread across HTTP views, serializers, and model methods. The same stock balances are updated in many places, usually without row locks or idempotency. Existing tests already reproduce a real ledger defect: after an MO is approved and then stopped, heat-tracked stock remains at 898 kg instead of returning to 1,000 kg.

The goal is not a rewrite. It is to make the existing behavior **correct under concurrency and retries**, give business rules an explicit home, and leave the public API, database data, permissions, and frontend contracts intact.

This design deliberately chooses a **modular monolith** over microservices. There is no measured requirement for distributed systems, and splitting the transaction boundary across services would make inventory integrity harder, not easier.

---

## 2. Goals, non-goals, anti-goals

### Goals

1. **Inventory integrity.** Every stock change is serialized, auditable, idempotent, and impossible to apply twice.
2. **Explicit boundaries.** Business rules live in domain/application modules, not in viewsets or serializers.
3. **Behavior preservation.** Existing URLs, response shapes, permissions, data, and migrations continue to work.
4. **Testability.** Critical workflows have unit, integration, API-contract, and concurrency tests.
5. **Security.** Least-privilege RBAC, server-side validation, no secrets in source, safe production defaults.
6. **Operability.** Structured logging, health checks, safe deployment, and documented migrations.
7. **Reviewable change.** Small, independently verifiable phases; no big-bang rewrite.

### Non-goals

- Rewriting the frontend in this phase.
- Moving Django models to new apps or renaming database tables.
- Introducing microservices, event sourcing, CQRS, Kafka, Celery, or a service mesh.
- Changing public API URLs or removing existing endpoints.
- Chasing a coverage number for its own sake.
- Replacing MySQL.

### Anti-goals (these make the project a failure even if it “works”)

- A refactor that changes stock behavior silently.
- A generic repository/abstraction layer that adds indirection without removing duplication.
- A distributed architecture introduced before a measured need.
- A migration that requires a long outage or cannot be rolled back.
- Duplicating business logic in both the old and new paths “temporarily” and leaving both live.

---

## 3. Constraints

- Django 5.2.6, DRF 3.15.2, SimpleJWT, MySQL 8.0 (`utf8mb4`), Redis available in production.
- Existing migrations and production data must be preserved.
- The frontend consumes current endpoints; response shapes must remain compatible.
- The repository has no CI, no backend linter, and only 46 tests (10 apps have placeholder tests).
- The custom test runner is MySQL-specific; local verification currently needs a MySQL database.
- Changes must be incremental and reviewable; no long-lived feature branches.
- Secrets must come from the environment; no credentials in source.

---

## 4. Current-state assessment

### 4.1 Baseline evidence

| Metric | Value |
|---|---|
| Python LOC (excluding migrations) | ~45,300 |
| Django apps | 20 |
| Models | 95 |
| Serializer classes | 183 |
| View classes / `@api_view` functions | 119 |
| Router registrations | 64 |
| `@action` endpoints | 179 |
| Tests | 46; 6 failures + 22 errors on an isolated SQLite harness |
| Apps with placeholder tests | 10 |
| `select_for_update` uses | 0 |
| Direct stock arithmetic mutations | 24 |
| Direct `InventoryTransaction` creates | 19 |
| Balance `get_or_create` / `save()` sites | 33 / 35 |
| `CheckConstraint` / `UniqueConstraint` | 1 / 0 |
| Broad `except Exception` / bare `except` | 169 / 7 |
| Production `print()` calls | 82 |
| Frontend (separate repo) | 63,700 LOC JavaScript, 221 files, zero TypeScript, zero tests |

`manage.py check` passes and `makemigrations --check --dry-run` reports no changes. The migration graph is consistent.

### 4.2 Priority findings

#### P0 — Inventory ledger is unsafe

- Two competing balances: `RMStockBalance` (legacy) and `RMStockBalanceHeat` (heat-tracked).
- `RawMaterialAllocation.lock_allocation()` decrements the heat balance.
- `ManufacturingOrderViewSet.change_status()` separately decrements the legacy balance.
- `ManufacturingOrder.stop_mo()` restores only one balance. Existing test `test_04_complete_flow` fails: heat stock stays at 898 kg instead of 1,000 kg.
- No row locks and no `F()` updates. Concurrent approvals can oversell; retries can double-post.
- No negative-stock guard.

#### P0 — Workflow transitions are not atomic or idempotent

- `change_status()` is not wrapped in a transaction and swallows allocation errors while still saving the new status.
- `start_batch()` logs an RM-lock failure and starts the batch anyway.
- `complete_batch()` is non-atomic and has no duplicate-completion guard.
- Process completion state is stored in `Batch.notes` and parsed with regex.
- `InventoryTransaction.idempotency_key` exists but is never used and is not unique.
- 85 direct status assignments; no transition table. Code uses statuses absent from the choices (`gm_approved`, `draft`, `approved`, `po_submitted`, batch `released`).

#### P0/P1 — Orphaned packing domain

- `packing_zone` is not in `INSTALLED_APPS`, has no migrations, and is not included in the root URLconf.
- The frontend calls `/api/packing-zone/*`, which currently returns 404.
- There are two FG stock models (`packing_zone.FGStock`, `fg_store`) and two `RMReturn` implementations.

#### P0/P1 — Security

- Hardcoded `SECRET_KEY` fallback; `DEBUG=True` default.
- No `SECURE_*`, HSTS, or SSL-redirect settings.
- Destructive reset/backup/restore endpoints (DEBUG-gated but present).
- Two `AllowAny` debug endpoints expose record counts.
- 30+ ad-hoc permission classes duplicated across apps.
- Frontend stores JWTs in `localStorage`; route authorization is client-side.
- Login sets an `HttpOnly` CSRF cookie while settings expect JavaScript to read it.

#### P1 — Architecture and quality

- God modules: `manufacturing/core_views.py` 4,970 LOC; `inventory/views.py` 1,598; `authentication/views.py` 1,053; `supervisor_views.py` 1,445.
- Business rules in views, serializers, and model methods simultaneously.
- 100 `SerializerMethodField`s, some issuing queries per row (heat summaries, returned coils, FG reservations).
- Mixed pagination (`PageNumberPagination` globally, `LimitOffsetPagination` in three viewsets).
- No OpenAPI schema, no API versioning, inconsistent error envelopes.
- No backend linter/formatter/type checker/CI.

#### P1 — Schema and deployment

- 1 `CheckConstraint`, 0 `UniqueConstraint`, 125 nullable `DecimalField`s.
- No `quantity >= 0` invariants, no idempotency uniqueness.
- Docker runs `runserver`, uses `collectstatic || true`, runs as root, and has no web healthcheck.
- Settings perform Redis pings and directory creation at import time.

---

## 5. Target architecture

### 5.1 Modular monolith and layer rules

Keep Django app labels and table names. Reorganize **inside** each app:

```
app/
  domain/          # Pure rules, value objects, state machines, exceptions
  application/     # Use-case services; own transaction boundaries
  selectors/       # Read-only query functions; no writes
  api/             # Viewsets, serializers, permissions, filters
  infrastructure/  # Adapters: ORM helpers, external services, file storage
  models.py        # Persistence models (kept where they are)
```

Dependency rule:

```
api  ->  application  ->  domain
api  ->  selectors
infrastructure  ->  domain
models  ->  domain (only value objects/enums, never application)
```

- Viewsets may call application services and selectors. They must not contain stock arithmetic, status transitions, or multi-model business rules.
- Serializers are DTOs. They may validate input shape, but not decide business outcomes.
- Models may contain persistence-level invariants and small derived properties. They must not perform cross-aggregate orchestration.
- Selectors return querysets or plain dicts and never mutate.

### 5.2 Domain map

| Domain | Physical apps today | Notes |
|---|---|---|
| Identity & access | `authentication` | Users, profiles, roles, sessions, engagement, breaks |
| Catalog | `products`, `inventory.RawMaterial`, `processes` | Products, raw materials, processes, BOM |
| Inventory | `inventory`, `fg_store`, `packing_zone` | Stock ledger, GRN, heat numbers, FG stock, returns |
| Procurement | `manufacturing.PurchaseOrder`, `inventory.GRNReceipt` | Split across apps; keep physical location for now |
| Manufacturing | `manufacturing` | MO, batch, process execution, allocations, rework, outsourcing |
| Quality | `quality`, `patrol` | Checks, traceability, patrol |
| Notifications | `notifications` | Workflow notifications |
| Reporting | `reporting` | Read models and reports |

The map is a boundary guide, not a mandate to move models. Moving `PurchaseOrder` or `GRNReceipt` would require table renames and migration risk; it is explicitly deferred. Cross-domain workflows live in the application service of the domain that owns the transaction (for example, `inventory.application.grn_service` owns GRN receipt and calls a procurement read selector).

**RM returns.** Two `RMReturn` models exist and both are used by the frontend. They are not duplicates to collapse in this phase:

- `manufacturing.RMReturn` records what a supervisor returned from a batch (batch, MO, material, heat, quantity, reason).
- `inventory.RMReturn` records the RM Store's disposition of returned material (accept back to stock, reject/scrap, return to vendor).

Both endpoints (`/api/manufacturing/rm-returns/`, `/api/inventory/rm-returns/`) keep their URLs and response shapes. A single `inventory.application.return_service` owns the stock effect; the manufacturing endpoint records the return, and the inventory endpoint records the disposition. The tables are consolidated only after both flows are covered by characterization tests and a data migration is designed separately.

### 5.3 Stock ledger

#### Single writer

Introduce `inventory/application/stock_service.py` as the **only** module allowed to mutate stock balances or create stock ledger entries. CI enforces this with a grep/AST check for direct `RMStockBalance*`, `HeatNumber.consumed_quantity_kg`, and `InventoryTransaction.objects.create` writes outside the service and migrations.

#### Ledger

Use the existing `InventoryTransaction` as the ledger, extended additively:

- `raw_material` — nullable FK to `RawMaterial` (currently only heat-level detail exists).
- `movement_type` — new field with precise semantics (`receipt`, `reservation`, `release`, `consumption`, `production`, `transfer`, `scrap`, `adjustment`, `dispatch`, `return`).
- `operation_key` — indexed, nullable `CharField`. One key per business operation (for example `mo:123:reserve`). All rows produced by one operation share it. Used for replay detection and audit grouping.
- `idempotency_key` — make unique (nullable; MySQL permits multiple NULLs). One key per ledger row.
- `metadata` — JSON field for structured audit data (heat breakdown, actor, reason, `direction` for adjustments).
- Indexes: `(raw_material, transaction_datetime)`, `(reference_type, reference_id)`, `(operation_key)`.
- Check constraint: `quantity > 0`. `quantity` is widened from 2 to 3 decimal places to match `RMStockBalanceHeat`, `HeatNumber`, and `ProductLocation`.

`transaction_type` remains for backward compatibility and existing reports. New code sets both `transaction_type` (closest legacy value) and `movement_type` (precise value).

**Ledger granularity.** One `InventoryTransaction` row per movement × material × heat × location. Each row keeps its existing 1:1 `InventoryTransactionHeat` detail, so no change to that model is required. A multi-heat or multi-line operation therefore produces several rows, all sharing one `operation_key` and each with its own `idempotency_key`:

```python
def make_key(operation_key, material_id, heat_id=None, location_id=None):
    return f"{operation_key}:{material_id}:{heat_id or 0}:{location_id or 0}"[:64]
```

`operation_key` is the replay guard: the service checks whether any row with that key exists before doing work. Because the whole operation runs in one `transaction.atomic()`, either all rows commit or none, so "some rows exist" means prior corruption, not a partial replay; the service raises and points at reconciliation.

**Partial receipts.** A GRN that is received in several deliveries must include the GRN line and heat number in the operation key (for example `grn:789:line:3:receive`), otherwise the second delivery would look like a replay. The key is derived from the line/heat, never from a timestamp.

**Adjustments and negative movements.** `quantity` stays strictly positive. Direction comes from `movement_type` plus `location_from`/`location_to`; for `adjustment` rows it comes from `metadata.direction` (`"in"`/`"out"`). Before adding the constraint, a pre-flight query counts rows with `quantity <= 0`. If any exist, the migration stops with a report and the operator decides whether to correct or void them; historical ledger rows are never rewritten automatically.

#### Balance projections and reservation semantics

- `HeatNumber` is the authoritative lot record: on-hand for a lot is `total_weight_kg - consumed_quantity_kg`. `HeatNumber.items` is descriptive metadata only; `consumed_quantity_kg` is authoritative for consumption.
- `RMStockBalanceHeat.total_available_quantity_kg` means **net available**: gross heat on-hand minus active reservations. This preserves its current meaning in the API (`available_quantity`) and its current use in stock checks and low-stock filters.
- `reserved_kg` is **derived from `RawMaterialAllocation` rows in `reserved` or `locked` status**, not stored in the balance row. This matches what `inventory/serializers.py` already computes for the UI.
- `RMStockBalance.available_quantity` is legacy and follows the same net-available meaning.
- The hold is created when an allocation is created in `reserved` status. `lock_allocation()` is a status/timestamp change only and **must not decrement stock again**. `release` restores available once.
- `update_from_heat_numbers()` is the source of the current bug: it recomputes gross on-hand and overwrites the net field, erasing reservations. It is replaced by `StockService.reconcile(raw_material)`, which computes `on_hand − active_reserved` and writes the projection.
- Consumption increments `HeatNumber.consumed_quantity_kg`; when the quantity was already reserved, it does **not** reduce available a second time.

`StockService` exposes a single read API:

```python
available_kg(raw_material) -> Decimal   # on_hand - active reservations
on_hand_kg(raw_material) -> Decimal     # sum of heat lot on-hand
reserved_kg(raw_material) -> Decimal    # sum of reserved + locked allocations
```

Reads prefer the heat projection, then fall back to legacy.

#### Stock ownership map

`StockService` is the only writer for every on-hand quantity in the system:

| Quantity | Authoritative source | Notes |
|---|---|---|
| RM on-hand | `HeatNumber` (`total_weight_kg − consumed_quantity_kg`) | Projected into `RMStockBalanceHeat` and legacy `RMStockBalance` |
| RM reserved | `RawMaterialAllocation` (`reserved` + `locked`) | Derived, not stored |
| RM at a location | `ProductLocation.quantity` | Transfer movements keep it in step |
| FG on-hand | `DispatchBatch.quantity_packed − quantity_dispatched` | `fg_store` has no `FGStock` model; this is the only FG balance today |
| FG reserved | `FGStockReservation` | Confirmed during Phase 1 inventory |
| Packing-zone FG | `packing_zone.FGStock` **if** `packing_zone` is wired | Currently unreachable; excluded unless the user chooses to wire it |

FG movements write the `DispatchBatch` counters and the ledger in the same transaction. `ProductLocation` rows for FG are updated when present.

#### Concurrency protocol

1. Open `transaction.atomic()`.
2. Lock the aggregate root: `RawMaterial.objects.select_for_update().get(pk=...)`. This serializes all stock changes for one material and avoids lock-order deadlocks.
3. Lock balance rows (`RMStockBalanceHeat`, then `RMStockBalance`) if they need updates.
4. Validate `quantity > 0`; for a new reservation, `available >= quantity`; for consumption not already reserved, the same check; for consumption from an existing reservation, verify the reservation is active and covers the quantity.
5. Update balances with `F()` expressions; `refresh_from_db()` before returning.
6. Create the ledger entry with a deterministic idempotency key.
7. Commit.

For multi-material operations, sort material IDs and lock in ascending order. Add a bounded retry (3 attempts) for MySQL deadlock error 1213.

#### Idempotency

- Deterministic operation keys by default: `f"{reference_type}:{reference_id}:{action}"` (for example `mo:123:reserve`, `batch:456:start`, `grn:789:line:3:receive`, `dispatch:101:confirm`).
- Optional `Idempotency-Key` header for client retries; if present, it replaces the default operation key.
- On replay, return the existing result and do not re-apply the balance change.
- `operation_key` is the service-level replay check; the unique `idempotency_key` is the final database guard. A duplicate insert is caught and converted into the same replay response.

#### Reservation vs consumption semantics

To preserve current behavior:

- `reserve` reduces **available** (on-hand minus reserved) and creates a reservation movement.
- `release` restores available and creates a release movement.
- `consume` increments `HeatNumber.consumed_quantity_kg` for traceability and creates a consumption movement. It does **not** reduce available again if the quantity was already reserved; callers pass `from_reservation=True`.
- `receive` increments available and creates a receipt movement; the `HeatNumber` row is created/updated by the GRN service.
- `transfer`, `scrap`, `dispatch`, and `adjustment` follow the same pattern with explicit movement types.

This removes the current double-decrement/release asymmetry.

#### Reconciliation

Add a management command `reconcile_stock_balances`:

- `--dry-run` reports drift between `HeatNumber`, `RMStockBalanceHeat`, and `RMStockBalance`.
- Without `--dry-run`, writes corrected projections inside a transaction and records an adjustment ledger entry per correction.
- Idempotent; safe to re-run.

### 5.4 State machines

Define explicit transition tables for:

- `ManufacturingOrder` (`submitted`, `mo_approved`, `rm_allocated`, `in_progress`, `on_hold`, `stopped`, `completed`, `cancelled`, `rejected`).
- `Batch` (`created`, `rm_allocated`, `in_process`, `quality_check`, `completed`, `packed`, `dispatched`, `cancelled`, `returned_to_rm`).
- `MOProcessExecution` (`pending`, `in_progress`, `completed`, `on_hold`, `cancelled`).
- `PurchaseOrder` (`po_initiated`, `po_approved`, `rm_in_progress`, `rm_completed`, `po_cancelled`).
- `GRNReceipt` (`pending`, `partial`, `completed`, `cancelled`).
- `RawMaterialAllocation` (`reserved`, `locked`, `released`, `swapped`).
- `DispatchBatch` / `DispatchTransaction` (existing choices).

Implementation: `app/domain/state_machines.py` with `ALLOWED_TRANSITIONS` and a `transition()` function that locks the row, validates, updates timestamps, and writes the existing history model atomically. Views call the application service, not the state machine directly.

**Status alias policy:** the code currently uses values absent from `choices` (`gm_approved`, `draft`, `approved`, `po_submitted`, batch `released`). Phase 1 adds these values to the choices so the data model reflects reality. Stored values are preserved; the state machine accepts both the alias and the canonical name and writes the canonical one for new transitions. Renaming existing data is a later migration with a compatibility period; this design does not silently rename existing rows.

| Alias found in code | Canonical | Notes |
|---|---|---|
| MO `gm_approved` | `mo_approved` | GM approval and manager approval share the same state |
| PO `draft` | `po_initiated` | PO created but not submitted |
| PO `approved`, `po_submitted` | `po_approved` | Both mean supplier-facing/approved |
| Batch `released` | `rm_allocated` (before start) or `in_process` (after start) | Resolve from timestamps/process state, not from the string alone |

The alias map lives in `domain/state_machines.py` and is the only place these equivalences are encoded.

### 5.5 API layer

- Keep all existing URLs and viewset names.
- Split god modules into `api/` modules but keep `core_views.py` as a re-export shim so external imports do not break.
- Keep serializer class names and response shapes. Additive fields only.
- Introduce selectors for list/detail queries to remove N+1.
- Add `drf-spectacular` for OpenAPI generation at `/api/schema/` (DEBUG only initially).
- Standardize error envelopes in Phase 3, with a compatibility period. Do not change existing error shapes in Phase 1.
- Pagination: keep current behavior in Phase 1. Standardize on one pagination class in Phase 3 after a frontend audit.

### 5.6 RBAC

- Central role codes and hierarchy in `authentication/domain/roles.py`.
- A single `HasRole` permission class with `required_roles` and optional object-level checks.
- Existing permission classes remain as thin aliases/subclasses delegating to the shared helper, preserving imports and behavior.
- Cache role lookups per user; invalidate on `UserRole` create/update/delete through service methods and signals.
- Move role checks out of viewsets and middleware into the permission/service layer incrementally.
- Add a permission matrix test for every role × critical endpoint.

### 5.7 Settings, observability, deployment

- Phase 0: add `microsprings_inventory_system/test_settings.py` for fast tests; make the custom runner fall back to Django's runner for non-MySQL engines.
- Phase 4: split settings into `config/settings/{base,dev,prod,test}.py`; keep `microsprings_inventory_system/settings.py` as a shim selected by `DJANGO_ENV`.
- Require `SECRET_KEY` in production; fail fast if missing.
- Add `SECURE_SSL_REDIRECT`, HSTS, secure cookies, and proxy header handling for production.
- Add request-ID middleware and structured JSON logs; no new logging dependency.
- Remove `AllowAny` debug endpoints and destructive HTTP endpoints; backups become management commands run by cron/operator.
- Docker: Gunicorn (not `runserver`), non-root user, healthcheck, no `|| true` on `collectstatic`.

### 5.8 Frontend boundary (follow-on, not this phase)

- Freeze API contracts with serializer snapshot tests.
- Later track: feature modules, strict TypeScript, one typed API client, httpOnly cookie auth, centralized errors, tests.
- Do not modify `../microsprings/msp-frontend` in this design.

---

## 6. Approaches considered

| Approach | What it does | Trade-off |
|---|---|---|
| **A. Backend-first modular monolith (chosen)** | Fix inventory integrity and extract backend domains in this repo; frontend later | Lowest risk to contracts; highest immediate correctness payoff; frontend debt remains temporarily |
| B. Full-stack, both repos | Also migrate the Next.js app to strict TS with a typed client and tests now | Best long-term outcome; roughly doubles scope; touches a separate repo; slower feedback |
| C. Integrity-only sprint | Only stock ledger, concurrency/idempotency, critical security | Fastest risk reduction; leaves god modules and structural debt |

**Chosen: A.** The existing tests already reproduce the ledger bug, so there is a concrete regression target. The frontend is a separate repository with 63k LOC; coupling its migration to the backend correctness work would delay the highest-risk fix.

---

## 7. Phased implementation plan

Each phase is a separate reviewable change set with its own verification. A phase does not start until the previous phase's exit criteria are met.

### Phase 0 — Safety net and baseline

**Deliverables**

- `microsprings_inventory_system/test_settings.py` (SQLite fast tests, locmem cache, MD5 hasher).
- Custom test runner falls back to `DiscoverRunner` when the database engine is not MySQL.
- Fix broken tests:
  - `create_user` calls missing `username`.
  - Missing `processes` URL namespace.
  - FG-store 403 permission setup.
- Add characterization tests for the five critical flows before changing them:
  - GRN receipt → stock.
  - MO approval → reserve → stop → release.
  - Batch start → process completion → batch completion.
  - RM return → disposition.
  - FG dispatch.
- `pyproject.toml` with ruff (E, F, I; exclude migrations; line length 120). Lint changed files only; do not reformat the whole repo.
- `requirements-dev.txt` with `pytest`, `pytest-django`, `pytest-cov`, `ruff`, `drf-spectacular` (schema is a runtime dependency; keep it in `requirements.txt` if exposed).
- `.github/workflows/backend.yml`: check, migration check, tests (SQLite), ruff on changed files.
- ADRs: `0001-modular-monolith.md`, `0002-stock-ledger.md`, `0003-idempotency.md`, `0004-test-strategy.md`.
- `docs/architecture/overview.md` and `docs/architecture/domain-map.md` with Mermaid diagrams.

**Exit criteria**

- `manage.py check` passes.
- `makemigrations --check` passes.
- The full suite passes on SQLite; MySQL integration suite runs in CI.
- The reproduced ledger bug is covered by a failing test that Phase 1 will fix (mark `xfail` with a link to the ADR until fixed, or keep it failing only in the Phase 1 branch).
- CI is green.

### Phase 1 — Inventory integrity

**Deliverables**

- `inventory/domain/stock.py`: movement types, `InsufficientStock`, pure validation.
- `inventory/application/stock_service.py`: `receive_rm`, `reserve_rm`, `release_rm`, `consume_rm`, `adjust_rm`, `transfer_batch`, `scrap`, `dispatch_fg`, `available_kg`, `on_hand_kg`, `reserved_kg`.
- `inventory/selectors/stock.py`: read-only stock queries.
- Migrations:
  - Add `InventoryTransaction.raw_material` (nullable).
  - Add `InventoryTransaction.movement_type` (blank/nullable; backfill optional).
  - Add `InventoryTransaction.metadata` (JSON default dict).
  - Add `InventoryTransaction.operation_key` (indexed, nullable).
  - Alter `InventoryTransaction.quantity` to 3 decimal places.
  - Alter `idempotency_key` to unique (pre-flight duplicates).
  - Add `quantity > 0` check constraint (pre-flight zero/negative rows).
  - Add indexes: `(raw_material, transaction_datetime)`, `(reference_type, reference_id)`, `(operation_key)`.
- Replace direct writes in priority order:
  1. GRN receipt/completion (`inventory/serializers.py`, `transaction_manager.py`).
  2. RM allocation/lock/release (`manufacturing/services/rm_allocation.py`, `manufacturing/models/allocations.py`, `manufacturing/models/manufacturing_order.py`).
  3. MO status transitions that reserve/release (`manufacturing/core_views.py`, `manufacturing/services/workflow.py`).
  4. Batch start/complete (`manufacturing/views/batch_views.py`, `manufacturing/core_views.py`).
  5. RM returns (`inventory/views.py`, `manufacturing/views/rm_return_views.py`).
  6. FG dispatch (`fg_store/views.py`).
- `reconcile_stock_balances` management command with `--dry-run`.
- Concurrency tests (MySQL):
  - Two concurrent reservations for the last available kg: exactly one succeeds.
  - Retry of the same reservation: one ledger entry, balance changed once.
  - Stop/release restores both heat and legacy balances exactly.
  - Batch completion replay does not double-consume.
- CI guard: grep/AST check that direct balance writes exist only in `stock_service.py` and migrations.

**Exit criteria**

- All Phase 0 tests green.
- New concurrency tests green on MySQL.
- `reconcile_stock_balances --dry-run` reports zero drift on the test fixtures.
- No direct stock arithmetic outside `StockService`.
- Existing API responses unchanged (snapshot tests).

### Phase 2 — Manufacturing domain extraction

**Deliverables**

- `manufacturing/domain/state_machines.py` for MO, Batch, ProcessExecution.
- `manufacturing/application/` services for MO lifecycle, batch lifecycle, process completion.
- `manufacturing/selectors/` for MO/batch lists and detail.
- Split `core_views.py` into `api/mo_views.py`, `api/po_views.py`, `api/batch_views.py`, `api/process_views.py`, `api/workflow_views.py`; keep `core_views.py` re-exports.
- Replace notes-regex process state with a `BatchProcessState` model:
  - `batch`, `process_execution`, `status`, `started_at/by`, `completed_at/by`, `metadata`.
  - `unique_together = (batch, process_execution)`.
  - Data migration parses existing `PROCESS_{id}_STATUS` notes; logs malformed entries.
- Move business logic out of `core_serializers.py` into services/selectors; keep serializer class names.
- State-transition tests for every allowed/forbidden transition.
- API contract snapshot tests for the split viewsets.

**Exit criteria**

- URLs and response shapes unchanged.
- No process state reads/writes from `Batch.notes`.
- All state transitions go through the state machine.
- Full suite green.

### Phase 3 — Procurement, read models, API consistency

**Deliverables**

- `inventory/application/grn_service.py` and `manufacturing/application/po_service.py`.
- Selectors for GRN, PO, heat numbers, transactions.
- Remove N+1 from the top 20 serializers (query-count tests with `assertNumQueries`).
- Standardize pagination on one class after a frontend audit; keep `results` envelope.
- Add `drf-spectacular` schema and contract tests.
- Apply the `packing_zone` decision: **retired**. Leave the code and schema untouched, document it, and remove the dead frontend calls in the follow-on track.
- Keep the two `RMReturn` tables, but move all stock effects into `return_service` and add contract tests for both endpoints. Table consolidation is a separate, later migration.
- Add missing indexes based on query analysis.

**Exit criteria**

- Query budgets met for the top list endpoints.
- OpenAPI schema generated and checked in.
- No RMReturn stock logic in views; both endpoints covered by contract tests.
- Full suite green.

### Phase 4 — Security, RBAC, deployment

**Deliverables**

- Central `HasRole` permission and role hierarchy; existing classes delegate.
- Role-cache invalidation on role changes.
- Permission matrix tests.
- Settings split; production fail-fast for `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`.
- Security headers, HSTS, secure cookies, proxy handling.
- Remove `AllowAny` debug endpoints and destructive HTTP endpoints.
- Backups as management commands + cron runbook.
- Docker: Gunicorn, non-root user, healthcheck, deterministic static collection.
- Request-ID middleware and structured logs.
- `python manage.py check --deploy` clean (with documented exceptions).

**Exit criteria**

- Permission matrix tests green.
- `check --deploy` clean.
- No secrets or debug endpoints in production settings.
- Container starts as non-root and passes healthcheck.

### Phase 5 — Frontend track (separate repository, follow-on)

Not part of this approval. When started:

- Strict TypeScript incremental migration (`allowJs` first).
- Feature modules: `src/features/<domain>/{api,components,hooks,types}`.
- One typed API client; remove duplicated base URLs and raw `fetch` calls.
- httpOnly cookie auth; remove tokens from `localStorage`.
- Centralized error handling; server/client boundaries.
- Vitest + Playwright for critical flows.
- CI with `tsc`, lint, build, tests.

### Phase 6 — Documentation and performance

- ADRs for every significant decision and trade-off.
- Dependency diagram and data-flow diagrams.
- API contracts and runbooks.
- Before/after measurements: query counts, p95 latency for top endpoints, test runtime.
- Remaining-risk register.

---

## 8. Data migration and rollback strategy

### Migration safety rules

1. Add nullable columns first; backfill in batches; add constraints last.
2. Pre-flight SQL checks before every constraint:
   - duplicate non-null `idempotency_key` values;
   - zero/negative `InventoryTransaction.quantity`;
   - negative balances;
   - orphaned rows.
3. Use `--dry-run` reconciliation before writing.
4. Back up the database before Phase 1 and Phase 2 migrations.
5. Run during a low-traffic window; keep migrations short and lock-aware.
6. Every migration is reversible where MySQL permits; data migrations are idempotent.
7. Keep legacy columns/tables until the new path has run in production for an agreed period.

### Rollback

- Code rollback is safe because new columns are additive and old code ignores them.
- Balance corrections are recorded as `adjustment` ledger entries; a rollback does not silently undo them.
- The reconciliation command can restore projections from `HeatNumber` and the ledger.
- If a constraint causes a production issue, drop it with a follow-up migration; do not edit applied migrations.

### Pre-flight runbook

`docs/runbooks/stock-reconciliation.md` documents:

- backup command;
- duplicate-key query;
- negative-balance query;
- dry-run reconciliation;
- expected output;
- approval and rollback steps.

---

## 9. Test strategy

### Test pyramid

| Layer | What | Tooling |
|---|---|---|
| Domain unit | State machines, stock rules, calculators | Django `SimpleTestCase` / pytest |
| Application integration | Services with real DB, transactions, locks | `TestCase` / `TransactionTestCase` |
| API contract | URL, status, response shape, permissions | DRF `APITestCase` + snapshots |
| Concurrency | Two transactions, retries, deadlocks | `TransactionTestCase` + threads on MySQL |
| E2E critical flows | GRN → MO → batch → dispatch | API-level flow tests |

### Test settings

- `test_settings.py`: SQLite in-memory for fast unit/API tests.
- MySQL integration profile for concurrency and migration tests.
- `pytest-django` with markers: `@pytest.mark.mysql`, `@pytest.mark.concurrency`, `@pytest.mark.slow`.
- Factories in `tests/factories.py` to remove repeated setup and fix the `create_user` defects.

### Coverage targets

- Critical services (`StockService`, state machines, allocation/release): **90%** line + branch.
- Overall backend: **60%** at Phase 1, **75%** by Phase 4.
- Coverage is a guardrail, not a goal; no tests that assert implementation details.

### CI gates

- `ruff check` on changed files.
- `manage.py check`.
- `makemigrations --check`.
- SQLite suite.
- MySQL suite (service container) for `mysql`/`concurrency` markers.
- OpenAPI schema diff (Phase 3+).

---

## 10. Observability and performance

- Structured JSON logs with request ID, user ID, endpoint, duration, status.
- Slow-query logging (threshold configurable).
- Query-count tests for the top 20 list/detail endpoints.
- Permission lookup cache with explicit invalidation.
- No Celery/Kafka until a measured requirement. Backups and reconciliation run as management commands via cron.
- Cache only after measurement; prefer selectors and indexes first.

---

## 11. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Behavior drift during extraction | High | Characterization tests before refactor; API snapshots; phase exit criteria |
| Data corruption during migration | High | Backup; dry-run; additive migrations; reconciliation command; rollback plan |
| Deadlocks from row locks | Medium | Lock aggregate root; sorted lock order; bounded retry; MySQL tests |
| Duplicate idempotency keys in existing data | Medium | Pre-flight query; backfill/null duplicates; unique constraint last |
| Negative balances in existing data | Medium | Pre-flight; reconcile before constraints; adjustment ledger |
| `packing_zone` schema unknown | Low | Retired for now; code untouched; revisit in Phase 3 |
| Frontend contract breakage | High | Freeze URLs/shapes; snapshot tests; no breaking changes in Phase 1–2 |
| Large diffs | Medium | Split by module; re-export shims; one flow per change set |
| Status aliases in production data | Medium | Add choices first; normalize later with a data migration |
| Test suite relies on MySQL | Medium | Add SQLite fast profile + MySQL CI service; fix test defects |

---

## 12. Decisions and open questions

### 12.1 Calls made by the architect (check these while reading)

These were settled from the code and the builder review. They do not need your input unless one is wrong for your business.

1. **Reservation semantics.** `RMStockBalanceHeat.total_available_quantity_kg` stays **net available** (gross on-hand minus active reservations). `reserved_kg` is derived from `RawMaterialAllocation` rows in `reserved`/`locked`. The hold is created at `reserved`; `lock_allocation()` no longer decrements. `update_from_heat_numbers()` is replaced by `StockService.reconcile()`.
2. **Ledger granularity.** One `InventoryTransaction` per movement × material × heat × location, sharing an `operation_key`; the existing 1:1 `InventoryTransactionHeat` stays. Partial GRN receipts include the line/heat in the key.
3. **Negative movements.** `quantity > 0`; direction comes from movement type and locations, or `metadata.direction` for adjustments. Existing zero/negative rows trigger a stop-and-report, not silent rewriting.
4. **FG ownership.** FG on-hand is `DispatchBatch.quantity_packed − quantity_dispatched` (there is no `FGStock` model in `fg_store`). `FGStockReservation` and `ProductLocation` are updated where present.
5. **Precision.** `InventoryTransaction.quantity` widens to 3 decimal places to match balances.
6. **Status aliases.** `gm_approved` → `mo_approved`; PO `draft` → `po_initiated`, `approved`/`po_submitted` → `po_approved`; batch `released` resolves to `rm_allocated` or `in_process`. Stored values are preserved during the compatibility period.
7. **RM returns.** `manufacturing.RMReturn` is the batch-level return record; `inventory.RMReturn` is the RM-store disposition record. Both endpoints stay; one service owns stock effects; the tables are not collapsed in this phase.
8. **Error and replay contract.** An invalid transition returns the existing `400` with `{"error": ...}`. A replay of a completed operation returns `200` with the current resource and does not re-apply stock. Existing inconsistent records are surfaced for reconciliation rather than auto-corrected.
9. **`RMStockBalance`.** Kept consistent through Phase 1, removed only after a reconciliation period and API audit.
10. **Dependencies.** `pytest-django`, `ruff`, and `drf-spectacular` are added; all are standard and replace manual work.
11. **MySQL.** Assume 8.0 everywhere; check constraints and `utf8mb4` depend on it.

### 12.2 Your answers

1. **`packing_zone`:** retired for now. Code and schema stay untouched; the dead frontend calls are removed in the follow-on track; revisit in Phase 3.
2. **In-flight work:** Phase 1–2 deploy while MOs and batches are mid-flight. The alias layer handles stored statuses; no quiescence window.
3. **Maintenance window:** a short low-traffic window is available. Phase 1 and Phase 2 migrations run with a verified database backup and a rollback plan.
4. **Other API consumers:** none. Contract tests cover the Next.js app only.

---

---

## 13. Definition of done (overall)

- All P0 findings fixed and covered by regression tests.
- One stock writer; no direct balance mutations outside it.
- Critical workflows idempotent and concurrency-safe on MySQL.
- Explicit state machines for MO, Batch, and ProcessExecution.
- API URLs and response shapes preserved; OpenAPI schema available.
- RBAC centralized; permission matrix tests green.
- Production settings fail fast on missing secrets; destructive endpoints removed.
- CI green: lint, checks, migrations, SQLite tests, MySQL integration tests.
- ADRs, domain map, data-flow diagrams, runbooks, and setup instructions updated.
- Remaining risks documented with owners and follow-ups.

---

## 14. Appendix A — Stock movement types

| Movement | Direction | Balance effect | Typical reference |
|---|---|---|---|
| `receipt` | in | available + | GRN |
| `reservation` | hold | available − | MO allocation |
| `release` | hold release | available + | MO stop/cancel |
| `consumption` | out | consumed + (available already held) | Batch/process |
| `production` | in | FG on-hand + | Batch completion |
| `transfer` | move | location − / location + | Process start |
| `scrap` | out | on-hand − | Scrap entry |
| `adjustment` | ± | on-hand ± | Reconciliation |
| `dispatch` | out | FG on-hand − | Dispatch confirmation |
| `return` | in | available + | RM return |

## 15. Appendix B — Lock order

1. `RawMaterial` (aggregate root), ascending ID.
2. `RMStockBalanceHeat`.
3. `RMStockBalance`.
4. `HeatNumber` (ascending ID if multiple).
5. `InventoryTransaction` insert.

For FG flows, lock `Product` or `FGStock` first, then the ledger insert. Never lock a child before its aggregate root.

## 16. Appendix C — Compatibility shims

- `manufacturing/core_views.py` re-exports the split viewsets.
- `manufacturing/core_serializers.py` re-exports the split serializers.
- Existing permission class names remain importable.
- Existing URL names and router basenames remain unchanged.
- `RMStockBalance` remains readable; writes go through `StockService`.
