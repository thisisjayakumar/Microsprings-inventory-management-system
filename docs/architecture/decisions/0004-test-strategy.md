# ADR 0004 — Test strategy and the expected-failure policy

- **Status:** Accepted
- **Date:** 2026-10-28
- **Supersedes:** none

## Context

The baseline suite had 47 tests with 6 failures and 22 errors, and 10 apps had
placeholder `tests.py` files. The custom test runner assumed MySQL and could not
run without a database server. There was no lint configuration, no CI, and no
convention for recording a known bug without hiding it.

## Decision

### Two test profiles

- **Fast profile:** SQLite in memory via
  `microsprings_inventory_system.test_settings`, locmem cache, MD5 password
  hashing, and the standard Django test runner. This is the default for local
  development and CI. It must run with only Python installed.
- **Concurrency profile:** MySQL, using the existing isolated-database runner.
  This profile is required for tests that prove row locking, deadlock retry, and
  real transaction isolation. Those tests are marked and run in a separate CI
  job when a MySQL service is available.

The custom runner delegates to Django's `DiscoverRunner` whenever the configured
engine is not MySQL.

### Test pyramid

- **Unit tests** for domain rules, state machines, and value objects. No database.
- **Integration tests** for application services with the ORM and transactions.
- **API tests** for permissions, validation, response shapes, and error handling.
- **Workflow tests** for the critical end-to-end paths: GRN receipt, MO approval
  and reservation, MO stop and release, batch process execution, RM return
  disposition, FG dispatch.

Every bug fix starts with a failing test. Characterization tests assert today's
behavior, including behavior that is wrong, so the fix has a visible target.

### Coverage targets

| Area | Phase 1 target |
|---|---|
| `StockService` and stock-changing services | 90% line and branch |
| Critical workflow tests | every transition and failure path listed above |
| Repository overall | 60% and rising each phase |

Coverage is a floor, not a goal. A passing test that asserts nothing is worse
than no test.

### Expected failures

A reproduced bug that is scheduled for a later phase is marked with
`@unittest.expectedFailure` and a comment naming the phase and the fix. This:

- keeps the suite green so regressions are visible;
- keeps the bug executable and documented;
- makes the fix self-verifying: removing the decorator must make the test pass.

Expected failures are reviewed every phase. They are not a place to park
unrelated or flaky tests.

### CI gates

On every push and pull request:

1. `python manage.py check`
2. `python manage.py makemigrations --check --dry-run`
3. The SQLite suite
4. Ruff on new files and on changed files that are already clean

Ruff starts with `E`, `F`, and `I`, ignoring `E501` and `F541` while legacy files
are brought into compliance. The ignore list is a ratchet and shrinks over time;
the whole repository is not reformatted in one commit.

## Consequences

**Positive**

- Contributors can run the suite in seconds without infrastructure.
- Concurrency behavior is still tested on the engine that production uses.
- Known bugs stay visible and are closed by deleting a decorator.
- CI catches check, migration, and import regressions on every change.

**Negative**

- SQLite differs from MySQL in locking, decimal handling, and constraint
  enforcement; some tests must be duplicated or marked MySQL-only.
- The expected-failure list needs periodic review or it becomes a graveyard.
- Linting changed legacy files exposes pre-existing debt that is out of scope
  for the current change.

## Alternatives considered

**Require MySQL for all tests.** Rejected. It makes the suite slow and blocks
contributors without a database server, which is how the baseline rotted.

**Skip known-broken tests.** Rejected. Skips hide the bug and tend to stay
forever; expected failures still execute the code path.

**Fix all lint debt before adding CI.** Rejected. A 3,000-finding reformat would
bury the refactor and make review impossible. The ratchet gets CI in place now.
