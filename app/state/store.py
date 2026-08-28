"""Async SQLite state store.

Holds everything needed to resume after a restart: projects, tasks, acceptance
criteria (inside tasks), checkpoints, execution logs, and per-day Claude-call
counts for budgeting. All access is async (aiosqlite).
"""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite

from app.state.models import (
    Checkpoint,
    Project,
    ProjectStatus,
    Task,
    TaskStatus,
    now_iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    slug          TEXT NOT NULL UNIQUE,
    requirement   TEXT NOT NULL,
    tech_stack    TEXT DEFAULT '',
    status        TEXT DEFAULT 'planning',
    workspace_path TEXT DEFAULT '',
    is_active     INTEGER DEFAULT 0,
    memory_json   TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_key      TEXT NOT NULL,
    goal          TEXT NOT NULL,
    requirements_json   TEXT DEFAULT '[]',
    acceptance_json     TEXT DEFAULT '[]',
    relevant_files_json TEXT DEFAULT '[]',
    status        TEXT DEFAULT 'pending',
    retry_count   INTEGER DEFAULT 0,
    order_index   INTEGER DEFAULT 0,
    result_json   TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    task_id       INTEGER,
    commit_hash   TEXT NOT NULL,
    label         TEXT DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exec_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER,
    task_id       INTEGER,
    category      TEXT DEFAULT 'system',
    message       TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claude_usage (
    day           TEXT PRIMARY KEY,   -- YYYY-MM-DD
    calls         INTEGER DEFAULT 0
);
"""


class StateStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)

    async def init(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON;")
            await db.executescript(SCHEMA)
            await db.commit()

    # ---------------- projects ----------------
    async def create_project(self, name: str, slug: str, requirement: str,
                             workspace_path: str) -> Project:
        ts = now_iso()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """INSERT INTO projects
                   (name, slug, requirement, workspace_path, status, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (name, slug, requirement, workspace_path,
                 ProjectStatus.PLANNING.value, ts, ts),
            )
            await db.commit()
            pid = cur.lastrowid
        return await self.get_project(pid)  # type: ignore[return-value]

    async def get_project(self, project_id: int) -> Project | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,))).fetchone()
        return _row_to_project(row) if row else None

    async def get_project_by_slug(self, slug: str) -> Project | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT * FROM projects WHERE slug=?", (slug,))).fetchone()
        return _row_to_project(row) if row else None

    async def list_projects(self) -> list[Project]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                "SELECT * FROM projects ORDER BY id DESC")).fetchall()
        return [_row_to_project(r) for r in rows]

    async def get_active_project(self) -> Project | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT * FROM projects WHERE is_active=1 LIMIT 1")).fetchone()
        return _row_to_project(row) if row else None

    async def set_active_project(self, project_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE projects SET is_active=0")
            await db.execute("UPDATE projects SET is_active=1, updated_at=? WHERE id=?",
                             (now_iso(), project_id))
            await db.commit()

    async def update_project_status(self, project_id: int, status: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?",
                             (status, now_iso(), project_id))
            await db.commit()

    async def update_project_memory(self, project_id: int, memory: dict) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE projects SET memory_json=?, updated_at=? WHERE id=?",
                             (json.dumps(memory), now_iso(), project_id))
            await db.commit()

    # ---------------- tasks ----------------
    async def add_task(self, project_id: int, task_key: str, goal: str,
                       requirements: list[str], acceptance: list[str],
                       relevant_files: list[str] | None = None,
                       order_index: int = 0) -> Task:
        ts = now_iso()
        ac = [{"text": a, "passed": False} for a in acceptance]
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """INSERT INTO tasks
                   (project_id, task_key, goal, requirements_json, acceptance_json,
                    relevant_files_json, status, order_index, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (project_id, task_key, goal, json.dumps(requirements),
                 json.dumps(ac), json.dumps(relevant_files or []),
                 TaskStatus.PENDING.value, order_index, ts, ts),
            )
            await db.commit()
            tid = cur.lastrowid
        return await self.get_task(tid)  # type: ignore[return-value]

    async def get_task(self, task_id: int) -> Task | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,))).fetchone()
        return _row_to_task(row) if row else None

    async def list_tasks(self, project_id: int) -> list[Task]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY order_index, id",
                (project_id,))).fetchall()
        return [_row_to_task(r) for r in rows]

    async def next_pending_task(self, project_id: int) -> Task | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                """SELECT * FROM tasks WHERE project_id=? AND status IN (?,?)
                   ORDER BY order_index, id LIMIT 1""",
                (project_id, TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value),
            )).fetchone()
        return _row_to_task(row) if row else None

    async def update_task(self, task_id: int, *, status: str | None = None,
                          retry_count: int | None = None,
                          result: dict | None = None,
                          acceptance: list[dict] | None = None) -> None:
        sets: list[str] = []
        vals: list = []
        if status is not None:
            sets.append("status=?")
            vals.append(status)
        if retry_count is not None:
            sets.append("retry_count=?")
            vals.append(retry_count)
        if result is not None:
            sets.append("result_json=?")
            vals.append(json.dumps(result))
        if acceptance is not None:
            sets.append("acceptance_json=?")
            vals.append(json.dumps(acceptance))
        if not sets:
            return
        sets.append("updated_at=?")
        vals.append(now_iso())
        vals.append(task_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", vals)
            await db.commit()

    # ---------------- checkpoints ----------------
    async def add_checkpoint(self, project_id: int, task_id: int | None,
                            commit_hash: str, label: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO checkpoints (project_id, task_id, commit_hash, label, created_at)
                   VALUES (?,?,?,?,?)""",
                (project_id, task_id, commit_hash, label, now_iso()))
            await db.commit()

    async def last_checkpoint(self, project_id: int) -> Checkpoint | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT * FROM checkpoints WHERE project_id=? ORDER BY id DESC LIMIT 1",
                (project_id,))).fetchone()
        if not row:
            return None
        return Checkpoint(id=row["id"], project_id=row["project_id"],
                          task_id=row["task_id"], commit_hash=row["commit_hash"],
                          label=row["label"], created_at=row["created_at"])

    # ---------------- logs ----------------
    async def log(self, message: str, project_id: int | None = None,
                  task_id: int | None = None, category: str = "system") -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO exec_logs (project_id, task_id, category, message, created_at)
                   VALUES (?,?,?,?,?)""",
                (project_id, task_id, category, message, now_iso()))
            await db.commit()

    async def recent_logs(self, project_id: int | None = None, limit: int = 15) -> list[str]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if project_id is None:
                q = "SELECT created_at, message FROM exec_logs ORDER BY id DESC LIMIT ?"
                args: tuple = (limit,)
            else:
                q = ("SELECT created_at, message FROM exec_logs WHERE project_id=? "
                     "ORDER BY id DESC LIMIT ?")
                args = (project_id, limit)
            rows = await (await db.execute(q, args)).fetchall()
        return [f"{r['created_at']}  {r['message']}" for r in rows]

    # ---------------- claude usage (budget) ----------------
    async def incr_claude_usage(self, day: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO claude_usage(day, calls) VALUES(?,1) "
                "ON CONFLICT(day) DO UPDATE SET calls = calls + 1", (day,))
            await db.commit()
            row = await (await db.execute(
                "SELECT calls FROM claude_usage WHERE day=?", (day,))).fetchone()
        return row[0] if row else 0

    async def claude_calls_today(self, day: str) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            row = await (await db.execute(
                "SELECT calls FROM claude_usage WHERE day=?", (day,))).fetchone()
        return row[0] if row else 0


def _row_to_project(row) -> Project:
    return Project(
        id=row["id"], name=row["name"], slug=row["slug"],
        requirement=row["requirement"], tech_stack=row["tech_stack"],
        status=row["status"], workspace_path=row["workspace_path"],
        is_active=bool(row["is_active"]), memory_json=row["memory_json"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _row_to_task(row) -> Task:
    return Task(
        id=row["id"], project_id=row["project_id"], task_key=row["task_key"],
        goal=row["goal"], requirements_json=row["requirements_json"],
        acceptance_json=row["acceptance_json"],
        relevant_files_json=row["relevant_files_json"], status=row["status"],
        retry_count=row["retry_count"], order_index=row["order_index"],
        result_json=row["result_json"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
