from app.git.repo import GitRepo


async def test_repo_lifecycle(tmp_path):
    ws = tmp_path / "proj"
    repo = GitRepo(ws)
    await repo.ensure_repo()
    assert await repo.is_repo()

    head0 = await repo.current_head()
    assert head0

    # create a file -> detected as changed
    (ws / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    assert await repo.has_changes()
    assert "index.html" in await repo.changed_files()

    # checkpoint commits it
    cp = await repo.checkpoint("first")
    assert cp
    assert not await repo.has_changes()

    # modify + commit, then rollback to checkpoint
    (ws / "index.html").write_text("<h1>changed</h1>", encoding="utf-8")
    commit = await repo.commit_all("update")
    assert commit and commit != cp

    ok = await repo.rollback(cp)
    assert ok
    assert (ws / "index.html").read_text(encoding="utf-8") == "<h1>hi</h1>"
