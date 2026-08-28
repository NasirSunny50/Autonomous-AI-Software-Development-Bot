from app.state.store import StateStore


async def _store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    await s.init()
    return s


async def test_project_and_task_lifecycle(tmp_path):
    s = await _store(tmp_path)
    p = await s.create_project("Shop", "shop", "build a shop", str(tmp_path / "ws"))
    assert p.id is not None

    await s.set_active_project(p.id)
    active = await s.get_active_project()
    assert active and active.id == p.id

    t = await s.add_task(p.id, "AUTH-001", "login", ["email", "password"],
                         ["valid login works", "invalid rejected"])
    assert t.status == "pending"

    nxt = await s.next_pending_task(p.id)
    assert nxt and nxt.id == t.id

    await s.update_task(t.id, status="completed")
    assert await s.next_pending_task(p.id) is None

    tasks = await s.list_tasks(p.id)
    assert len(tasks) == 1 and tasks[0].status == "completed"


async def test_checkpoints_and_usage(tmp_path):
    s = await _store(tmp_path)
    p = await s.create_project("A", "a", "req", str(tmp_path / "ws"))
    await s.add_checkpoint(p.id, None, "abc1234", "init")
    cp = await s.last_checkpoint(p.id)
    assert cp and cp.commit_hash == "abc1234"

    day = "2026-08-28"
    assert await s.incr_claude_usage(day) == 1
    assert await s.incr_claude_usage(day) == 2
    assert await s.claude_calls_today(day) == 2


async def test_unique_slug_conflict(tmp_path):
    s = await _store(tmp_path)
    await s.create_project("A", "dup", "r", "ws")
    assert await s.get_project_by_slug("dup") is not None
    assert await s.get_project_by_slug("missing") is None
