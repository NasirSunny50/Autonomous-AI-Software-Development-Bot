# 🤖 AI Dev Bot — Autonomous Software Development Assistant

Your personal, **Telegram-controlled** software engineer. Give it a high-level
requirement; it plans, breaks the work into tasks, uses **Claude Code** to write the
code, runs builds/tests/linting and **Playwright** browser checks, automatically
fixes failures, keeps **Git checkpoints**, and reports back — asking for your input
only when it genuinely needs to. It also sends a **daily update** and answers
questions about your projects on demand.

> **Goal:** minimize your day-to-day involvement in your projects. The bot does the
> work; you give requirements, approve risky actions, and unblock true deadlocks.

---

## The single cost rule

**Claude Code is the only thing you ever pay for** (via your existing Claude
Pro/Max subscription). Everything else runs on **free tiers or open-source**:
Telegram, Gemini, Groq, OpenRouter, Playwright, SQLite, Docker. The bot will
**never silently use a paid API** — if only a paid path remains, it pauses and asks.

---

## How it thinks (the important idea)

| Layer | Role | Cost |
|-------|------|------|
| 🐍 **Python** | **Commander** — decides *what* runs and *when* (build/test/lint/git), reads exit codes, drives the retry/state machine | 0 |
| 🧠 **Claude Code** | **Brain + Hands** — all real reasoning: analysis, planning, coding, complex debugging | subscription |
| 🆓 **Free models** | **Leaf helpers only** — log→digest, error triage, NL→JSON, fallback. Never on the decision path | 0 |

Deterministic Python does the deciding; Claude Code does the thinking; free models
only do cheap text glue. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Setup

### 1. Prerequisites (verified on this machine)
- Python **3.10** (recommended for the bot's venv) — Node 24, Git, Docker already present.
- **Claude Code standalone CLI** (required, currently not installed):
  ```bash
  npm install -g @anthropic-ai/claude-code
  claude            # run once, log in with your Claude Pro/Max account
  ```
  > The Claude *Desktop* agent mode cannot be scripted by the bot — the standalone
  > CLI is what provides headless `claude -p` mode.

### 2. Install the bot
```bash
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1        # PowerShell
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
```
Fill in at minimum `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, and
`CLAUDE_COMMAND`. Free AI keys (Gemini/Groq/OpenRouter) are optional but recommended.
See comments in `.env.example`.

### 4. Run
```bash
python run.py
```

---

## Telegram commands

```
/start     intro & auth check          /pause     stop starting new tasks
/help      command list                /resume    continue from saved state
/new       start a new project         /stop      graceful shutdown
/projects  list & switch project        /retry     retry the failed task
/status    current project & task       /test      run the quality gate now
/logs      recent activity             /rollback  revert to last checkpoint
/ask       ask about the project       /deploy    deploy to a free host
/autonomy  view/set autonomy level     /switch    switch active project
```
You can also just type a requirement in plain language, e.g.
*"Build a modern e-commerce site with auth, product listing, cart and checkout."*

---

## Auto-deploy (optional, free hosts)

After a project passes final QA, the bot can publish it and send you a **live URL**.
It's opt-in — set `DEPLOY_PROVIDER` and one token in `.env`:

| Provider | Best for | Token(s) |
|----------|----------|----------|
| `vercel` | Next.js / SSR / most frameworks | `VERCEL_TOKEN` |
| `cloudflare` | static / exported sites | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` |
| `surge` | quick static sites | `SURGE_TOKEN` |

Use `DEPLOY_PROVIDER=auto` to pick whichever token you set. All run on **free tiers**.
Tokens are passed to the CLI via environment only — never logged, never committed.
Preview deploys run automatically at `high` autonomy; **production deploys always ask
for approval** (inline button). Deploy manually anytime with `/deploy` (or `/deploy prod`).

## Security

- Only your configured Telegram user id can control the bot; all others are rejected.
- Secrets stay in `.env` (git-ignored) — never logged, never sent to AI, never committed.
- Every command is validated to run inside the active project's workspace.
- Destructive actions (delete DB, force-push, production deploy, destructive
  migrations) always require explicit approval, regardless of autonomy level.
- Retries, Claude-call counts, and subprocess time are all bounded — no runaway loops.

---

## Project layout

```
app/            orchestrator, telegram, ai, claude, browser, testing, git,
                memory, security, state, utils
workspaces/     each managed project gets its own isolated folder here
logs/           structured logs (system / ai / claude / browser)
data/           SQLite state (resume-after-restart)
docs/           ARCHITECTURE.md, ROADMAP.md
config/         optional config files
run.py          entrypoint
```

---

## Status & roadmap

Built incrementally in 10 phases — see [`docs/ROADMAP.md`](docs/ROADMAP.md).
**Phases 0–10 are implemented and tested** (94 unit/integration tests, ruff clean).
Browser QA is real-proven (boots a dev server → headless Chromium → HTTP/console/
screenshot). The only remaining item is the *stretch* auto-deploy goal, left as
documented future work rather than an untested claim.

To go live you supply: the Claude Code CLI (`@anthropic-ai/claude-code`) and a
`.env` with your Telegram token + user id. Everything else runs on free tiers.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `claude: command not found` | Install `@anthropic-ai/claude-code`; set `CLAUDE_COMMAND` (often `claude.cmd` on Windows). |
| Bot ignores your messages | Check `TELEGRAM_ALLOWED_USER_ID` matches your numeric id (@userinfobot). |
| pip wheel build errors | Use the Python **3.10** venv, not 3.14. |
| "Provider unavailable / paused" | A free provider hit its quota; add another free key or wait — the bot never falls back to paid. |
