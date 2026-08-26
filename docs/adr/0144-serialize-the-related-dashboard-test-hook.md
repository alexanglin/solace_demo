# ADR-0144: Serialize the related dashboard test hook

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0057's commit-stage execution mechanics

## Context

The commit-stage dashboard unit hook uses `vitest related` so staged TypeScript paths select the tests
that reach them. Pre-commit may split a large filename set into multiple command batches and execute
those batches concurrently. Vitest already schedules the selected files within each process, so a large
dashboard commit started three independent jsdom pools on the same workstation. Tests that passed both
alone and in the authoritative single-process full dashboard gate then exceeded their five-second
timeouts or missed fixture acknowledgement before the wait bound.

Increasing test timeouts would hide resource contention and weaken feedback. Replacing related selection
with an unconditional commit-stage full run would duplicate the pre-push gate and spend more of the
commit-stage budget than the staged change requires.

## Decision

Set `require_serial: true` on the `vitest-related` pre-commit hook.

The hook continues to receive staged `.ts` and `.tsx` paths and continues to invoke exactly
`vitest related --run --`. Pre-commit may still partition a large argument set, but it must execute those
partitions one at a time. Vitest remains free to schedule the tests selected inside each partition.

Keep the complete dashboard unit/coverage and deterministic integration suites as unconditional
pre-push gates. Do not increase individual test timeouts or suppress the affected tests to accommodate
parallel pre-commit processes.

## Consequences

- Large TypeScript commits cannot create competing Vitest/jsdom process pools through pre-commit
  filename batching.
- Small commits keep import-related selection and its fast feedback.
- A large commit can run more than one related partition sequentially, so its commit-stage duration may
  rise; the pre-push suites and continuous integration remain the verification authority.
- A quality-gate test pins serialization so the concurrency defect cannot silently return.

## Alternatives considered

- **Increase test timeouts.** Rejected because the tests were waiting on CPU-starved scheduling rather
  than slower product behavior.
- **Run the complete dashboard suite on every commit.** Rejected because the unconditional full suite
  already runs at pre-push and related selection is otherwise sound.
- **Allow concurrent batches and reduce Vitest workers.** Rejected because two independent schedulers
  would remain able to oversubscribe the workstation, and the hook's correctness would depend on the
  size of pre-commit's argument partitions.
