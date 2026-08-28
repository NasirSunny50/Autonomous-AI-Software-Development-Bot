# AI Dev Bot — Architecture

> **North star:** reduce the owner's involvement in building/maintaining software to
> almost nothing. The bot does the work, gives a **daily update**, and answers
> questions on demand. The owner only (1) gives a requirement, (2) approves genuinely
> risky actions, (3) unblocks true deadlocks.

---

## 1. The one cost rule

**Claude Code is the only thing we ever pay for.** It runs on the owner's existing
Claude Pro/Max subscription — no per-token API billing. Every other component
(Telegram, Gemini, Groq, OpenRouter, Playwright, SQLite, Docker) runs on a **free
tier or is open-source**. The code must never silently call a paid API. Provider
selection is deterministic and always prefers free; if only a paid path remains, the
bot **pauses and asks** instead of spending.

Because Claude Code is the only cost, the real budget metric is **"how many times did
we invoke Claude Code"**, not free-provider token counts. Hard caps live in `.env`
(`CLAUDE_MAX_CALLS_PER_PROJECT`, `CLAUDE_MAX_CALLS_PER_DAY`).

---

## 2. Corrected responsibility split (the key design decision)

An earlier instinct was to let the free models make decisions and "command" Claude
Code. That is **inverted** — a weaker model steering a stronger one produces bad
directions, which makes Claude Code iterate more, which costs more. So:

```
        ┌─────────────────────────────────────────────┐
        │  🐍 PYTHON (deterministic)  =  THE COMMANDER  │
        │  Decides WHAT runs and WHEN. Spends 0 tokens. │
        └───────────────────────┬─────────────────────┘
                                │ issues one focused task
                                ▼
        ┌─────────────────────────────────────────────┐
        │  🧠 CLAUDE CODE  =  THE BRAIN + HANDS         │
        │  All real reasoning: analysis, planning,      │
        │  architecture, coding, complex debugging.     │
        └───────────────────────┬─────────────────────┘
                                │ (only for cheap text glue)
                                ▼
        ┌─────────────────────────────────────────────┐
        │  🆓 FREE MODELS  =  LEAF HELPERS ONLY          │
        │  log→digest, error triage, NL→JSON, fallback. │
        │  NEVER on the critical decision path.         │
        └─────────────────────────────────────────────┘
```

### 🐍 Python (the Commander) — no AI, fully deterministic
- Decides when to build / test / lint / typecheck / run Playwright / commit.
- Reads exit codes; detects changed files with `git diff --name-only` (never by
  parsing AI output).
- Runs the bounded retry loop, the state machine, pause/resume, rollback.
- Enforces budgets, autonomy rules, working-directory validation, command allow/deny.

### 🧠 Claude Code (the Brain + Hands) — all the thinking
- Requirement analysis, project plan, task breakdown.
- Architecture decisions, writing/editing code, complex debugging, code review.
- Driven **headlessly**: `claude -p "<task>" --output-format json` (or stream-json),
  so Python can send a task and capture stdout / stderr / exit code.

### 🆓 Free models (Gemini → Groq → OpenRouter) — cheap glue, optional
- Summarize long build/test logs into a short Telegram digest (save a Claude call).
- Quick error-type triage / classification.
- Turn a free-form Telegram message into a structured requirement JSON.
- **Fallback** if Claude Code is rate-limited/unavailable → notify & pause, never pay.

---

## 3. Component map

```
                         OWNER (Telegram)
                              │
                              ▼
                     ┌──────────────────┐
                     │  app/telegram/   │  auth, commands, inline buttons,
                     │  (interface)     │  concise updates, daily digest
                     └────────┬─────────┘
                              ▼
                     ┌──────────────────┐
                     │ app/orchestrator │  THE COMMANDER (state machine)
                     └───┬────┬────┬────┘
        ┌────────────────┘    │    └────────────────┐
        ▼                     ▼                     ▼
 ┌────────────┐        ┌────────────┐        ┌────────────┐
 │ app/state  │        │ app/claude │        │ app/ai     │
 │ SQLite:    │        │ headless   │        │ free-model │
 │ projects,  │        │ Claude Code│        │ router +   │
 │ tasks, AC, │        │ wrapper    │        │ budget mgr │
 │ logs, ckpt │        └─────┬──────┘        └────────────┘
 └────────────┘              ▼
                     ┌──────────────┐
                     │  PROJECT     │  (in workspaces/<project>/)
                     └──────┬───────┘
             ┌──────────────┴──────────────┐
             ▼                             ▼
     ┌───────────────┐             ┌───────────────┐
     │ app/testing   │             │ app/browser   │
     │ build/test/   │             │ Playwright QA │
     │ lint/typecheck│             │ (Phase 5)     │
     └───────┬───────┘             └───────┬───────┘
             └──────────────┬──────────────┘
                            ▼
                   ┌──────────────────┐
                   │  QUALITY GATE     │  deterministic verdict
                   └───┬───────────┬──┘
                  PASS │           │ FAIL
                       ▼           ▼
                 app/git      auto-debug → focused fix task
                 checkpoint   → Claude Code → retest (≤ MAX_RETRIES)
```

Support modules: `app/memory` (compact project state), `app/security` (auth,
secret hygiene, command guard), `app/utils` (logging, process runner).

---

## 4. Autonomous task lifecycle

```
requirement (Telegram)
  → [free model] parse to structured requirement JSON (cheap)
  → [Claude Code] analyze + plan + break into tasks (1 call)
  → tasks stored in SQLite with acceptance criteria
  → FOR EACH task (Python drives):
       git checkpoint
       [Claude Code] implement the single task (1 call)
       Python: detect changed files (git diff)
       QUALITY GATE (deterministic): build → test → lint → typecheck → browser
         PASS → git commit → next task
         FAIL → collect evidence (error, trace, failing test, diff, screenshot)
                [free model] triage error type (cheap)
                [Claude Code] focused fix task (evidence only, NOT whole project)
                retest → repeat up to MAX_RETRIES
                still failing → rollback + "HUMAN REVIEW REQUIRED" to Telegram
  → all tasks done → FINAL QA → final report to Telegram
```

**Token discipline:** Claude Code never receives the whole project — only the current
task, the relevant files, and (on failure) the specific evidence. The compact
**project memory** (`app/memory`) carries stack/architecture/known-issues between
tasks so context stays small.

---

## 5. Safety & security invariants

- **Telegram allow-list:** only `TELEGRAM_ALLOWED_USER_ID` is obeyed; all others rejected.
- **Secrets:** live in `.env` only; never logged, never sent to any AI model, never committed.
- **Working-directory guard:** every shell/Claude command is validated to run inside the
  active project's `workspaces/<project>/` — never leak into another project.
- **Command guard:** deny-list for destructive shell (`rm -rf /`, force-push, DB drops,
  `curl|sh`, etc.). These always require explicit approval regardless of autonomy level.
- **Git safety:** checkpoint before major tasks; commit on success; rollback on repeated
  failure. Never auto force-push, never auto-delete branches.
- **Bounded everything:** retries capped by `MAX_RETRIES`; Claude calls capped by budget;
  every subprocess has a timeout. No infinite loops.
- **Optional isolation:** Docker is available on this machine → generated projects can run
  inside a container for stronger sandboxing (free).

---

## 6. Resume-after-restart

All durable state is in SQLite (`data/state.db`): projects, tasks, task status,
acceptance criteria, checkpoints, retry counts, logs, project memory. On startup the
orchestrator loads the last incomplete task and continues. Stop/pause persist the
current task, step, last command, last result, and retry count so nothing is lost.

---

## 7. Environment (verified on this machine — 2026-08-28)

| Tool         | Version / note |
|--------------|----------------|
| Python       | 3.14.0 present **and** 3.10.0 present → bot venv uses **3.10** for wheel compatibility |
| Node / npm   | v24.11.1 / 11.6.2 |
| Git          | 2.33.0 (identity: Nasir Sunny) |
| Docker       | 29.6.2 (available → optional free sandbox) |
| Claude Code  | ⚠️ standalone CLI **not yet on PATH** — install `@anthropic-ai/claude-code`; current session is Desktop agent mode, which cannot be scripted |
| Playwright   | not yet installed (Phase 5) |
| OS / shell   | Windows 10, PowerShell primary (bash also available) |
