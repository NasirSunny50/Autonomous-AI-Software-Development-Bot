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
- [ ] Typed config loader (`app/config.py`) from `.env` via pydantic-settings
- [ ] Structured logging (`app/utils/logging.py`) → `logs/` + console
- [ ] SQLite state store (`app/state/`): projects, tasks, checkpoints, logs
- [ ] Deterministic subprocess runner (`app/utils/process.py`): capture stdout/stderr/exit/timeout
- [ ] Claude Code headless wrapper (`app/claude/`): `claude -p ... --output-format json`
- [ ] Git helper (`app/git/`): init/checkpoint/commit/rollback + `git diff --name-only`
- [ ] Telegram interface (`app/telegram/`): auth allow-list, `/start /help /new /status`
- [ ] Orchestrator skeleton (`app/orchestrator/`): requirement → 1 task → Claude Code → commit → report
- [ ] `run.py` entrypoint; unit tests for deterministic pieces; manual E2E once token+CLI ready

## Phase 2 — Free AI provider abstraction  `[ ]`
- [ ] `AIProvider` base + Gemini / Groq / OpenRouter implementations (httpx, async)
- [ ] Deterministic router with health checks + free-only fallback chain
- [ ] `token_manager.py`: per-task call budgets + Claude-call caps; pause instead of paying
- [ ] Used for: log→digest, error triage, NL→requirement JSON

## Phase 3 — Claude Code integration (full)  `[ ]`
- [ ] Structured coding-task templates (goal / requirements / AC / relevant files / instructions)
- [ ] Structured result parsing; completion vs failure detection; execution logging
- [ ] Timeout + uncontrolled-execution guards

## Phase 4 — Terminal execution + Git safety  `[ ]`
- [ ] Working-directory validation per project
- [ ] Command allow/deny guard (block destructive ops)
- [ ] Checkpoint-before-task / commit-on-success / rollback-on-repeated-failure

## Phase 5 — Playwright browser QA  `[ ]`
- [ ] Install Playwright; reusable utils: start dev server, navigate, click, fill, submit
- [ ] Validate URL/text/elements; screenshots; console + network error capture
- [ ] Basic responsiveness + broken-link checks

## Phase 6 — Automatic debugging / self-healing  `[ ]`
- [ ] Evidence collector (error, trace, failing test, console, screenshot, git diff)
- [ ] Focused fix-task generator (evidence only — never whole project)
- [ ] Retry loop ≤ `MAX_RETRIES` → else rollback + human-review message

## Phase 7 — Quality gate + acceptance criteria  `[ ]`
- [ ] Per-project-type gate: build / test / lint / typecheck / browser / AC
- [ ] Individual acceptance-criteria tracking; task "COMPLETED" only when gate passes
- [ ] Risk-based AI review (auth, payments, DB migrations, security only)

## Phase 8 — Token optimization + quota management  `[ ]`
- [ ] Context minimizer (relevant files/sections only)
- [ ] Compact project memory read/write
- [ ] Analysis cache (no duplicate AI calls); deterministic-checks-first ordering
- [ ] Scaffold via CLIs (`create-next-app`, etc.) instead of AI-generated boilerplate

## Phase 9 — Security + authorization + autonomy  `[ ]`
- [ ] Telegram owner authorization hardening
- [ ] Autonomy levels (low/medium/high) + always-approve destructive/production actions
- [ ] Secret-hygiene checks; inline approve/reject buttons

## Phase 10 — End-to-end + daily updates + deploy  `[ ]`
- [ ] Full E2E: requirement → plan → build → QA → fix → commit → final report
- [ ] Daily digest scheduler (`DAILY_UPDATE_TIME`)
- [ ] Q&A over project state ("answer what I ask")
- [ ] (Stretch, free) auto-deploy to a free host + live preview URL in Telegram

---

### Owner's success test (the finish line)
Owner sends a requirement in Telegram → bot analyzes → plans → creates tasks →
uses Claude Code to build → runs build/test/Playwright → auto-fixes failures →
commits checkpoints → runs final QA → reports completion. Owner only gives the
requirement, approves risky actions, and unblocks true deadlocks — and gets a
**daily update** plus **answers on demand**.
