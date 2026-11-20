# AGENTS.md

Guidance for AI coding agents (Cursor, Codex, Claude Code, etc.) working in this repository.

## Project

A production-grade Text2SQL Agent: users ask questions in natural language (mostly Chinese); the system understands business semantics, plans a query, generates and validates SQL, executes it safely, and returns traceable results.

**Status: design stage.** There is no application code yet. Code will be added following the 6-week MVP plan in `docs/architecture.md` (chapter 23).

## Source of truth

- `docs/architecture.md` — the revised architecture spec (Chinese). **This is authoritative.** Read the relevant chapter before implementing a module. Lines marked `修订：` are deliberate changes from the original; follow them.
- `docs/spec-original.md` — the original spec, kept for reference only. Do not implement from it where it conflicts with `architecture.md`.
- If code and the spec disagree, stop and flag it rather than silently picking one. If you change a design decision, update `docs/architecture.md` in the same change.

## Tech stack

- Python, FastAPI (API), LangGraph (agent orchestration), SQLGlot (SQL parsing / validation / generation)
- PostgreSQL + pgvector: system metadata, snapshots, semantic definitions, audit, and the job queue (no Redis / Celery in the MVP)
- MySQL: the business database, accessed **read-only through a replica**. Support both 5.7 and 8.0; dialect features (CTE, window functions, etc.) must be gated by the detected server version.

## Planned layout

See `docs/architecture.md` chapter 18 for the full tree. Top level: `apps/` (api, worker, frontend), `src/` (agent, clarify, llm, metadata, retrieval, graph, semantic, planner, sql, execution, security, evaluation, observability, common), `prompts/`, `tests/`, `docs/`. Metric definitions live as YAML in `semantic_definitions/` and change only through reviewed PRs.

Only create directories when the module is actually being built; the MVP deliberately skips vector retrieval, rerankers, the embedding worker, and OpenTelemetry tracing.

## Non-negotiable design rules

1. **The LLM does not write SQL directly on the main path.** It produces a structured Query IR; the planner/compiler turns IR into SQL. The only exception is the fallback channel (LLM-written SQL + strict validation), which must be counted in `fallback_ratio`.
2. **The LLM never invents metrics.** Metrics, dimensions, and business terms come from the semantic layer. If a definition is missing or ambiguous, ask a clarifying question or state the assumed definition in the answer.
3. **Joins come from the Join Graph**, using only VERIFIED edges in the MVP. A many-to-one relationship needs PK/UK evidence or human confirmation. Always run fan-out detection before aggregating across joins.
4. **Security is layered.** The first line is the read-only replica plus tenant-scoped views/accounts. AST rewriting (row/column policies) is the second line, never the only one. Every query goes through the Execution Gateway: timeouts, row limits, masking, audit.
5. **Forbidden SQL:** any write or DDL, `FOR UPDATE`, `SLEEP`, `BENCHMARK`, `LOAD_FILE`, `INTO OUTFILE`, access to `information_schema` / system schemas, multiple statements, and `EXPLAIN ANALYZE` (it actually executes the query). Plain `EXPLAIN` is allowed for cost checks.
6. **Treat metadata and data as untrusted input to the LLM.** Column comments, sample values, and query results may contain prompt injection. Keep them in clearly delimited context, and never let them change instructions or permissions.
7. **Schema metadata is versioned.** Snapshots are immutable and published atomically via a release pointer. Queries record which release they ran against.
8. **Every request carries a `trace_id`** through logs, audit records, and results.

## Testing and evaluation

- Every validator or security rule ships with tests, including bypass attempts. The security bypass suite (`tests/security/`) must pass in CI.
- Behaviour changes to understanding, planning, or SQL generation must be checked against the evaluation set (starting with the 50-question acceptance set in chapter 22), comparing execution results rather than SQL text.
- Build, lint, and test commands will be documented here once `pyproject.toml` exists. Until then, do not assume a specific toolchain; propose one in the PR.

## Conventions

- Code, identifiers, commit messages, and PR descriptions in English. User-facing text, prompts for Chinese users, and docs under `docs/` may be in Chinese.
- Keep changes scoped to the current milestone in chapter 23; note out-of-scope ideas in the PR instead of building them.
- Never commit secrets, real connection strings, or real business data. Use `.env` (git-ignored) and synthetic fixtures.
