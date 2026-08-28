import sys

from app.utils.process import run_command


async def test_run_ok():
    res = await run_command([sys.executable, "-c", "print('hello')"])
    assert res.ok
    assert "hello" in res.stdout
    assert res.exit_code == 0


async def test_run_nonzero():
    res = await run_command([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert not res.ok
    assert res.exit_code == 3


async def test_missing_binary():
    res = await run_command(["definitely-not-a-real-binary-xyz"])
    assert not res.ok
    assert res.exit_code == 127


async def test_timeout():
    res = await run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.5
    )
    assert res.timed_out
    assert not res.ok
