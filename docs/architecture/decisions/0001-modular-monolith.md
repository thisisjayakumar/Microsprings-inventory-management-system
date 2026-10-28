# ADR 0001 — Refactor as a modular monolith

- **Status:** Accepted
- **Date:** 2026-10-28
- **Supersedes:** none

## Context

The backend is a single Django project with 20 apps, roughly 45k lines of Python,
95 models, and a 5k-line `manufacturing/core_views.py`. Stock, manufacturing,
procurement, and reporting rules are interleaved in viewsets and serializers. The
immediate problems are correctness (stock drifts), operability (no reliable test
suite), and change safety (no clear module boundaries), not scale.

The frontend is a separate Next.js repository and consumes the existing HTTP API.
There are no other API consumers.

## Decision

Keep one deployable Django application and refactor it into a **modular monolith**.

- Django app labels, database table names, and existing URLs are preserved.
- Each app is reorganized internally into `domain/`, `application/`, `selectors/`,
  `api/`, and `infrastructure/`, with `models.py` staying in place.
- Dependencies point inward: `api -> application -> domain`,
  `api -> selectors`, and `infrastructure -> domain`.
- Cross-domain workflows live in the application service of the domain that owns
  the transaction. Models do not orchestrate other aggregates.
- Physical model moves (for example `PurchaseOrder` or `GRNReceipt`) are deferred;
  the domain map is a boundary guide, not a table-rename plan.

The layer rules and the domain map are documented in
[`docs/architecture/overview.md`](../overview.md) and
[`docs/architecture/domain-map.md`](../domain-map.md).

## Consequences

**Positive**

- One deployment, one database, and one transaction boundary for workflows that
  must be atomic (MO approval, GRN completion, dispatch).
- The refactor can proceed app by app without a flag day or a data migration.
- Existing API contracts, permissions, and frontend integrations remain valid.
- Tests and ownership become tractable because each app has explicit layers.

**Negative**

- Layer boundaries are enforced by convention, review, and CI checks rather than
  by process isolation.
- A single process still shares memory, connections, and failure modes.
- Developers must resist placing business rules back into viewsets or serializers.

## Alternatives considered

**Microservices.** Rejected for this phase. The current correctness problems are
inside single-database transactions; splitting them would turn atomic workflows
into distributed sagas, add operational surface, and slow delivery. Revisit only
if independent scaling or team ownership demands it.

**Integrity-only sprint.** Rejected. Fixing the stock ledger without boundaries
would leave the same coupling that produced the bug, and the next change would
reintroduce it.

**Rewrite.** Rejected. A rewrite would discard working behavior and data while
the existing flows are still being characterized. Incremental refactoring keeps
the system shippable after every phase.
