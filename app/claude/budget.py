"""Claude-call budget guard.

Because Claude Code is the ONLY paid worker, the meaningful budget metric is the
number of Claude Code invocations — not free-provider tokens. This guard enforces
hard per-day and per-project caps. When a cap is hit, the orchestrator pauses and
notifies the owner instead of spending more.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.state.store import StateStore


@dataclass
class BudgetDecision:
    allowed: bool
    reason: str
    calls_today: int
    calls_project: int


class ClaudeBudget:
    def __init__(self, store: StateStore, max_per_day: int, max_per_project: int):
        self.store = store
        self.max_per_day = max_per_day
        self.max_per_project = max_per_project

    async def check(self, project_call_count: int) -> BudgetDecision:
        day = date.today().isoformat()
        today = await self.store.claude_calls_today(day)
        if self.max_per_day and today >= self.max_per_day:
            return BudgetDecision(False, f"daily Claude cap reached ({self.max_per_day})",
                                  today, project_call_count)
        if self.max_per_project and project_call_count >= self.max_per_project:
            return BudgetDecision(False,
                                  f"project Claude cap reached ({self.max_per_project})",
                                  today, project_call_count)
        return BudgetDecision(True, "ok", today, project_call_count)

    async def record(self) -> int:
        """Record one Claude call against today's counter; returns new total."""
        return await self.store.incr_claude_usage(date.today().isoformat())
