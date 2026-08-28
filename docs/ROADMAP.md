# AI Dev Bot — Build Roadmap (phase by phase)

Rule for every phase: **build → test → fix → commit → continue.** Never move forward
with broken functionality. Never leave placeholder functions for core features.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done & tested

---

## Phase 0 — Foundation & docs  `[x]`
- [x] Inspect environment (Python/Node/Git/Docker/Claude Code)
- [x] Project structure + `.gitignore` + `.env.example` + `requirements.txt`
- [x] `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `README.md`
- [x] `git init`

## Phase 1 — Vertical slice (thin end-to-end)  `[~]`
> Prove the whole pipeline with the smallest real path before thickening it.
- [x] Typed config loader (`app/config.py`) from `.env` via pydantic-settings
- [x] Structured logging (`app/utils/logging.py`) → `logs/` + console
- [x] SQLite state store (`app/state/`): projects, tasks, checkpoints, logs, usage
- [x] Deterministic subprocess runner (`app/utils/process.py`): capture stdout/stderr/exit/timeout
- [x] Claude Code headless wrapper (`app/claude/`): `claude -p ... --output-format json`
- [x] Claude-call budget guard (`app/claude/budget.py`): per-day/per-project caps
- [x] Git helper (`app/git/`): init/checkpoint/commit/rollback + change detection
- [x] Security guard (`app/security/`): workspace validation + destructive-command deny-list
- [x] Telegram interface (`app/telegram/`): auth allow-list + `/start /help /new /status /projects /logs /autonomy /pause /resume /stop`
- [x] Orchestrator skeleton (`app/orchestrator/`): requirement → 1 task → Claude Code → commit → report
- [x] `run.py` entrypoint + config banner
- [x] 30 unit tests green (process, state, git, security, text, claude-parse); ruff clean
- [ ] **Manual E2E** — needs owner to: install Claude Code CLI, create `.env` (token + user id). Then send a requirement in Telegram.

## Phase 2 — Free AI provider abstraction  `[x]`
- [x] `AIProvider` base + Gemini / Groq / OpenRouter (httpx, async, injectable transport)
- [x] Deterministic router with health checks + cooldowns + free-only fallback chain
- [x] `token_manager.py`: per-task call budgets + response cache (Claude money-cap is separate)
- [x] `glue.py`: log→digest, error triage, NL→requirement JSON — each with a deterministic
      fallback so no free provider configured ⇒ still works, still $0
- [x] Wired into orchestrator: requirement → free-model parse → tech_stack/features → project memory
- [x] 25 more unit + integration tests (providers via MockTransport, router fallback, glue,
      full orchestrator slice with fake worker). Total **55 tests green**, ruff clean.

## Phase 3 — Claude Code integration (full)  `[x]`
- [x] Structured coding-task templates (`app/claude/prompts.py`): goal / requirements /
      AC / relevant files / instructions + concise trailing-report contract
- [x] Structured result parsing (`app/claude/report.py`): CHANGED_FILES / BUILD / TESTS /
      SUMMARY, status normalization, advisory-only (gate is the real authority)
- [x] Completion vs failure detection: `ClaudeOutcome` (success/failed/timeout) + report
      folded into task result and Telegram summaries
- [x] Execution logging to `logs/claude.log` (start/outcome/exit/duration)
- [x] Uncontrolled-execution guards: hard timeout, prompt-size cap (60k chars),
      cwd-must-exist check, Windows `.cmd` handling
- [x] 10 more tests (report parsing, worker outcome, run guards). Total **65 green**, ruff clean.

## Phase 4 — Terminal execution + Git safety  `[x]`
- [x] `app/testing/commands.py` `ProjectCommandRunner`: workspace validation per project
- [x] Command allow/deny guard (blocks destructive ops → require approval)
- [x] Checkpoint-before-task / commit-on-success / rollback-on-repeated-failure (in orchestrator)

## Phase 5 — Playwright browser QA  `[x]`
- [x] Playwright + Chromium installed; `app/browser/playwright_qa.py`: start dev server,
      detect URL, open headless, screenshot, console + network error capture
- [x] Deterministic pass/fail; graceful skip when unavailable (Python 3.10-compatible)
- [x] **Real-proven**: booted a node server → Chromium → HTTP 200 → screenshot saved

## Phase 6 — Automatic debugging / self-healing  `[x]`
- [x] Evidence collector: gate failures + git diff + free-model triage (never whole project)
- [x] Focused fix-task generator (`build_fix_prompt`, evidence only)
- [x] Retry loop ≤ `MAX_RETRIES` → else rollback to checkpoint + HUMAN REVIEW message

## Phase 7 — Quality gate + acceptance criteria  `[x]`
- [x] `app/testing/quality_gate.py`: node (install/build/test/lint/typecheck) + python +
      browser; deterministic verdict = the real authority on completion
- [x] Acceptance-criteria marked satisfied only when the gate passes
- [x] Risk-based AI review (auth/payments/db/security only) via free model, non-blocking

## Phase 8 — Token optimization + quota management  `[x]`
- [x] `app/claude/context.py` context minimizer (only listed files, trimmed, budgeted)
- [x] `app/memory/project_memory.py` compact memory read/write
- [x] Response cache (Phase 2) + deterministic-checks-first ordering
- [x] Planner prompts Claude to scaffold via official CLIs instead of hand-written boilerplate

## Phase 9 — Security + authorization + autonomy  `[x]`
- [x] Telegram owner allow-list (rejects all others; callbacks re-checked)
- [x] `app/security/autonomy.py` levels (low/medium/high) + always-approve destructive
- [x] `app/security/secrets.py` hygiene (detect + redact); inline approve/reject buttons

## Phase 10 — End-to-end + daily updates + deploy  `[x]`
- [x] Full E2E spine: requirement → plan → build → gate → self-heal → commit → final report
- [x] Daily digest scheduler (`DAILY_UPDATE_TIME`, dependency-free asyncio loop)
- [x] Q&A over project state (`/ask`, uses memory + logs, free-model)
- [ ] (Stretch, free) auto-deploy to a free host + live preview URL — **documented as future work**;
      not implemented so as not to claim an untested capability

---

### Owner's success test (the finish line)
Owner sends a requirement in Telegram → bot analyzes → plans → creates tasks →
uses Claude Code to build → runs build/test/Playwright → auto-fixes failures →
commits checkpoints → runs final QA → reports completion. Owner only gives the
requirement, approves risky actions, and unblocks true deadlocks — and gets a
**daily update** plus **answers on demand**.
