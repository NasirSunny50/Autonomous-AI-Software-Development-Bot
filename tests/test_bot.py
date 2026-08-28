import asyncio

from app.config import Settings
from app.telegram.bot import TelegramBot


class StubBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)


class StubApp:
    def __init__(self):
        self.bot = StubBot()


class StubCQ:
    def __init__(self, data):
        self.data = data
        self.edited = None

    async def answer(self):
        pass

    async def edit_message_text(self, text):
        self.edited = text


class StubUpdate:
    def __init__(self, cq, user_id):
        self.callback_query = cq
        self._uid = user_id

    @property
    def effective_user(self):
        return type("U", (), {"id": self._uid})()


def _bot(uid=42):
    s = Settings()
    s.telegram_allowed_user_id = uid
    b = TelegramBot(s, store=None)
    b.app = StubApp()
    return b


def test_authorized():
    b = _bot(42)
    assert b._authorized(StubUpdate(None, 42))
    assert not b._authorized(StubUpdate(None, 999))


def test_seconds_until_daily_in_range():
    b = _bot()
    secs = b._seconds_until_daily()
    assert 0 < secs <= 86400


async def test_approval_approved():
    b = _bot(42)
    task = asyncio.create_task(b.request_approval("deploy to prod?"))
    await asyncio.sleep(0.02)
    token = next(iter(b._pending_approvals))
    cq = StubCQ(f"ap:{token}:1")
    await b.on_callback(StubUpdate(cq, 42), None)
    assert await task is True
    assert cq.edited.startswith("✅")


async def test_approval_rejected():
    b = _bot(42)
    task = asyncio.create_task(b.request_approval("delete db?"))
    await asyncio.sleep(0.02)
    token = next(iter(b._pending_approvals))
    await b.on_callback(StubUpdate(StubCQ(f"ap:{token}:0"), 42), None)
    assert await task is False


async def test_callback_from_unauthorized_ignored():
    b = _bot(42)
    fut = asyncio.get_event_loop().create_future()
    b._pending_approvals["tok"] = fut
    await b.on_callback(StubUpdate(StubCQ("ap:tok:1"), 999), None)  # wrong user
    assert not fut.done()
