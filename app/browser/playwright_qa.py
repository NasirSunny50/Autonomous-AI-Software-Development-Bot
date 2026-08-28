"""Browser QA with Playwright.

Starts the project's dev server, opens it in headless Chromium, and checks the
app actually loads — capturing console errors, failed requests, and a screenshot.
Everything degrades gracefully: if Playwright isn't installed, or the project has
no dev/preview script, or the server URL can't be detected, the check returns
`skip` (never a false `fail`, never a crash).

This is deterministic QA (Python decides pass/fail from signals). A vision model
only gets involved later, and only after meaningful UI changes (Phase 8 rule).
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from app.testing.quality_gate import CheckResult, shell
from app.utils.logging import get_logger
from app.utils.text import truncate

log = get_logger("browser")

URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|\[::1\]):\d+", re.IGNORECASE)
# Order of preference for a "run the app" script.
DEV_SCRIPTS = ["dev", "start", "preview", "serve"]


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def pick_dev_script(scripts: dict) -> str | None:
    for name in DEV_SCRIPTS:
        if name in scripts:
            return name
    return None


class BrowserQA:
    def __init__(self, logs_dir: Path | str, startup_timeout: float = 45.0):
        self.logs_dir = Path(logs_dir)
        self.startup_timeout = startup_timeout

    async def smoke_test(self, project_path: Path | str, info: dict) -> CheckResult:
        if info.get("type") != "node":
            return CheckResult("browser", "skip", "not a web project")
        if not playwright_available():
            return CheckResult("browser", "skip", "playwright not installed")
        script = pick_dev_script(info.get("scripts", {}))
        if not script:
            return CheckResult("browser", "skip", "no dev/preview script")

        path = Path(project_path)
        pm = info.get("pm", "npm")
        proc, url = await self._start_server(path, shell([pm, "run", script]))
        if url is None:
            await self._stop(proc)
            return CheckResult("browser", "skip", "dev server URL not detected")

        try:
            return await self._check_page(path, url)
        finally:
            await self._stop(proc)

    # ---- dev server ----
    async def _start_server(self, path: Path, args: list[str]):
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, cwd=str(path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
        except (FileNotFoundError, OSError) as exc:
            log.warning("dev server failed to start: %s", exc)
            return None, None

        try:
            url = await asyncio.wait_for(self._read_url(proc),
                                         timeout=self.startup_timeout)
        except asyncio.TimeoutError:
            url = None
        return proc, url

    @staticmethod
    async def _read_url(proc):
        """Read the dev server's stdout until a localhost URL appears (or EOF)."""
        while True:
            line = await proc.stdout.readline()
            if not line:
                return None
            m = URL_RE.search(line.decode(errors="replace"))
            if m:
                return m.group(0).replace("[::1]", "localhost")

    async def _stop(self, proc) -> None:
        if proc is None:
            return
        try:
            if os.name == "nt":
                # Kill the whole tree (npm spawns node children).
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            else:
                proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=10)
        except (ProcessLookupError, asyncio.TimeoutError):
            pass

    # ---- page check ----
    async def _check_page(self, path: Path, url: str) -> CheckResult:
        from playwright.async_api import async_playwright

        console_errors: list[str] = []
        failed_requests: list[str] = []
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        shot = self.logs_dir / f"{path.name}-screenshot.png"

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" else None)
            page.on("requestfailed",
                    lambda r: failed_requests.append(f"{r.method} {r.url}"))
            try:
                resp = await page.goto(url, wait_until="load", timeout=30000)
            except Exception as exc:  # navigation failure = real fail
                await browser.close()
                return CheckResult("browser", "fail", "navigation error",
                                   truncate(str(exc), 500))
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await page.screenshot(path=str(shot))
            status = resp.status if resp else 0
            await browser.close()

        problems = []
        if status >= 400 or status == 0:
            problems.append(f"HTTP {status}")
        if console_errors:
            problems.append(f"{len(console_errors)} console error(s)")
        if failed_requests:
            problems.append(f"{len(failed_requests)} failed request(s)")

        if problems:
            detail = "; ".join(problems)
            output = truncate("\n".join(console_errors + failed_requests), 1200)
            return CheckResult("browser", "fail", detail, output)
        log.info("browser QA passed | %s | screenshot=%s", url, shot)
        return CheckResult("browser", "pass", f"loaded {url} (HTTP {status})")
