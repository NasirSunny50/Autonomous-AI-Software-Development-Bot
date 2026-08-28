"""Natural-language conversation layer.

Lets the owner just chat with the bot — no slash commands needed. A free model
classifies each message into an intent and drafts a friendly reply; if no free
provider is available it falls back to deterministic keyword heuristics, so plain
conversation always works.

Intents:
  new_project     - the owner wants to build something
  status          - asking about current progress/state
  switch_project  - wants to work on a different project
  question        - a question about the active project
  confirm         - yes / start / go ahead
  cancel          - no / stop / never mind
  help            - what can you do
  chitchat        - general talk / greetings
"""
from __future__ import annotations

from app.utils.jsonparse import extract_json

INTENTS = {"new_project", "status", "switch_project", "question",
           "confirm", "cancel", "help", "chitchat"}

_SYSTEM = (
    "You are the friendly assistant of an autonomous software-development bot. "
    "The bot builds software projects for the owner. Classify the owner's message "
    "into one intent and write a short, warm reply IN THE OWNER'S OWN LANGUAGE/STYLE "
    "(Banglish/Bengali/English are all fine). Never use slash commands in the reply."
)


class ConversationEngine:
    def __init__(self, router=None):
        self.router = router

    async def interpret(self, message: str, context: dict) -> dict:
        """Return {intent, project_name, reply}."""
        if self.router is not None and self.router.configured():
            data = await self._ai(message, context)
            if data:
                return data
        return self._fallback(message, context)

    async def _ai(self, message: str, context: dict) -> dict | None:
        ctx = _format_context(context)
        prompt = (
            f"{_SYSTEM}\n\nCurrent state:\n{ctx}\n\n"
            f"Owner's message: \"{message}\"\n\n"
            "Respond ONLY with JSON:\n"
            '{"intent": "<new_project|status|switch_project|question|confirm|cancel|help|chitchat>", '
            '"project_name": "<short name if they want to build something, else empty>", '
            '"reply": "<a short friendly reply in the owner\'s language>"}'
        )
        resp = await self.router.complete(prompt, kind="normal", max_tokens=300)
        if not resp.ok:
            return None
        data = extract_json(resp.text)
        if not isinstance(data, dict) or data.get("intent") not in INTENTS:
            return None
        return {
            "intent": data["intent"],
            "project_name": str(data.get("project_name") or "").strip(),
            "reply": str(data.get("reply") or "").strip(),
        }

    # ---- deterministic fallback ----
    def _fallback(self, message: str, context: dict) -> dict:
        low = f" {message.lower().strip()} "
        has_active = bool(context.get("active_project"))

        def any_in(words):
            return any(w in low for w in words)

        # Keep confirm words unambiguous (avoid " koro " — appears in "kaj koro").
        if any_in([" yes ", " yeah ", " yep ", " ok ", " okay ", " haan ", " han ",
                   " start ", " shuru ", " confirm ", " accha ", " correct ",
                   " thik ache "]):
            return {"intent": "confirm", "project_name": "",
                    "reply": "Okay! 👍"}
        if any_in([" no ", " na ", " nah ", " cancel ", " thak ", " bad ", " stop ",
                   " skip "]):
            return {"intent": "cancel", "project_name": "",
                    "reply": "Thik ache, cancel korlam. 🙂"}
        if any_in([" status ", " progress ", " obostha ", " koddur ", " kotdur ",
                   " ki obostha ", " kototuku ", " update "]):
            return {"intent": "status", "project_name": "", "reply": ""}
        if any_in([" switch ", " onno project ", " change project ", " different project ",
                   " select project "]):
            return {"intent": "switch_project", "project_name": "", "reply": ""}
        if any_in([" build ", " create ", " make ", " develop ", " banao ", " baniye ",
                   " bananor ", " toiri ", " banabo ", " bana ", " app ", " website ",
                   " site ", " system "]):
            return {"intent": "new_project", "project_name": "",
                    "reply": "Bujhlam! Ekta project shuru korchi. 🚀"}
        if any_in([" help ", " ki koro ", " ki paro ", " what can you "]):
            return {"intent": "help", "project_name": "", "reply": ""}
        if has_active:
            return {"intent": "question", "project_name": "", "reply": ""}
        return {"intent": "chitchat", "project_name": "",
                "reply": "Ami tomar software banate pari 🙂 — ki banate chao bolo?"}


def _format_context(context: dict) -> str:
    if not context.get("active_project"):
        active = "No active project."
    else:
        active = (f"Active project: {context['active_project']} "
                  f"(status: {context.get('status', '?')}, "
                  f"tasks {context.get('tasks_done', 0)}/{context.get('tasks_total', 0)}). "
                  f"Bot busy: {context.get('busy', False)}.")
    projects = context.get("projects") or []
    plist = ("Known projects: " + ", ".join(projects)) if projects else "No projects yet."
    return f"{active}\n{plist}"
