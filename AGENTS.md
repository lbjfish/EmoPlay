# Project Agent Rules

## Scope and ownership

- Work only inside the repository root unless the task explicitly asks to read the source materials.
- Never modify the supplied source-material directory; it is read-only evidence.
- Respect the file ownership assigned in the subtask. Do not edit another agent's owned directory.
- When an interface is missing, document the need in your agent ledger instead of changing another agent's files.

## Subagent model routing

- The root task remains on `gpt-5.6-sol`.
- Use `gpt-5.6-terra` at `max` for complex implementation or audit work.
- Use `gpt-5.6-luna` for lighter bounded work.
- If any GPT-5.6 subtask hits a rate limit, retry that responsibility with `gpt-5.5` at `xhigh`, the highest supported effort exposed by the collaboration runtime.

## Traceability

- Every active agent writes `work/agent-ledger/<task>.md` with scope, files, commands, tests, defects, decisions, limitations, and handoff. `work/` is Git-ignored; root distills durable engineering facts into the public `docs/development/` logs.
- Add discovered defects to `docs/development/bug_log.md` only when you own the fix; otherwise report them in your ledger for the root agent.
- Tests must prove behavior. A generated file or green command without coverage of the requirement is not completion evidence.
- Public reflection records observation, decision, action, result, and correction. Do not write hidden chain-of-thought.

## Safety

- Never print, copy, save, or commit API keys, Authorization values, cookies, full private endpoints, or user global configuration.
- Do not call paid/live providers unless the root task explicitly assigns a smoke test.
- Placeholder, fixture, prompt-only, and local draft outputs must set `final_eligible=false`.
- No upload, GitHub push, activation, or final submission.

## Engineering

- Prefer Python standard library for the offline core.
- SQLite is optional; FileStorage must remain a complete no-SQLite path.
- Use UTC-aware datetimes and stable, canonical JSON.
- Preserve append-only history for calls, decisions, story events, and checkpoints.
- Run targeted tests and report exact commands and outputs.

