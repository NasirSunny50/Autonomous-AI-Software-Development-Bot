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
