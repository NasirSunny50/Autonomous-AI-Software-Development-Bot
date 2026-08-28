"""Autonomy levels & risk-based approval.

Three levels: low / medium / high. Destructive or production-impacting actions
ALWAYS require approval regardless of level. High-risk areas (auth, payments,
migrations, security, ...) are flagged so the orchestrator can spend an AI review
only where it matters — cheap UI tweaks rely on deterministic tests instead.
"""
from __future__ import annotations

import enum


class ActionRisk(str, enum.Enum):
    NORMAL = "normal"          # ordinary dev work
    RISKY = "risky"            # touches sensitive area (auth/payments/db/security)
    DESTRUCTIVE = "destructive"  # deletes data, force-push, prod deploy, etc.


# High-risk areas that justify an AI code review (and count as RISKY actions).
HIGH_RISK_KEYWORDS = [
    "auth", "login", "password", "session", "token", "jwt", "oauth",
    "authorization", "permission", "role", "admin",
    "payment", "checkout", "billing", "stripe", "invoice", "refund", "price",
    "migration", "schema", "database", "sql", "drop", "delete",
    "security", "encrypt", "secret", "api key", "credential",
    "deploy", "production", "env",
]


def classify_risk(text: str) -> ActionRisk:
    low = (text or "").lower()
    if any(k in low for k in HIGH_RISK_KEYWORDS):
        return ActionRisk.RISKY
    return ActionRisk.NORMAL


def is_high_risk(text: str) -> bool:
    return classify_risk(text) == ActionRisk.RISKY


class ApprovalPolicy:
    def __init__(self, level: str = "high"):
        self.level = (level or "high").lower()

    def needs_approval(self, risk: ActionRisk, *, major: bool = False) -> bool:
        # Destructive/production actions are never automatic.
        if risk == ActionRisk.DESTRUCTIVE:
            return True
        if self.level == "high":
            return False
        if self.level == "medium":
            return risk == ActionRisk.RISKY
        # low: ask before risky OR any "major" action (e.g. starting a project)
        return risk == ActionRisk.RISKY or major
