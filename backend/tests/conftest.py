import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/manychat_test"
)
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.dev")
os.environ.setdefault("ADMIN_PASSWORD", "password123")
os.environ.setdefault("INSTAGRAM_APP_SECRET", "ig-secret")
os.environ.setdefault("WEBHOOK_VERIFY_TOKEN", "verify-me")

from collections.abc import AsyncIterator  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.engine import handlers  # noqa: E402
from app.engine.runner import EngineContext  # noqa: E402
from app.instagram.client import InstagramAPIError  # noqa: E402
from app.models import IGAccount  # noqa: E402
from app.security import encrypt_secret  # noqa: E402

ACCOUNT_IG_ID = "17841400000000001"


class FakeInstagram:
    """Records every call; tests can queue errors and set profile data."""

    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, str], dict[str, Any]]] = []
        self.public_replies: list[tuple[str, str]] = []
        self.profiles: dict[str, dict[str, Any]] = {}
        self.send_errors: list[InstagramAPIError] = []
        self.profile_error: InstagramAPIError | None = None
        # Newest first, like the real /media listing
        self.media: list[dict[str, Any]] = []
        self.media_calls = 0
        self._counter = 0

    async def send_message(self, recipient: dict[str, str], message: dict[str, Any]) -> dict:
        if self.send_errors:
            raise self.send_errors.pop(0)
        self.sent.append((recipient, message))
        self._counter += 1
        return {"recipient_id": recipient.get("id", "x"), "message_id": f"m_out_{self._counter}"}

    async def reply_to_comment(self, comment_id: str, text: str) -> dict:
        self.public_replies.append((comment_id, text))
        return {"id": f"reply_{comment_id}"}

    async def get_user_profile(self, igsid: str) -> dict:
        if self.profile_error:
            raise self.profile_error
        return self.profiles.get(igsid, {"username": f"user_{igsid}", "name": "Test User"})

    async def get_media(self, media_id: str) -> dict:
        self.media_calls += 1
        for media in self.media:
            if media["id"] == media_id:
                return media
        raise api_error(100, status=404, message="Unsupported get request")

    async def list_media(self, limit: int = 24, after: str | None = None) -> dict:
        self.media_calls += 1
        return {"data": self.media[:limit]}

    # helpers
    def texts(self) -> list[str]:
        out = []
        for _, message in self.sent:
            if "text" in message:
                out.append(message["text"])
            else:
                payload = message["attachment"]["payload"]
                out.append(payload.get("text") or payload["elements"][0]["title"])
        return out


def api_error(code: int, subcode: int | None = None, status: int = 400, message: str = "err"):
    error: dict[str, Any] = {"code": code, "message": message}
    if subcode:
        error["error_subcode"] = subcode
    return InstagramAPIError(status, {"error": error})


@pytest.fixture(scope="session", autouse=True)
async def _schema() -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncIterator[None]:
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def fake_ig(monkeypatch: pytest.MonkeyPatch) -> FakeInstagram:
    fake = FakeInstagram()
    monkeypatch.setattr(handlers, "client_factory", lambda account: fake)
    return fake


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
async def account(session) -> IGAccount:
    acc = IGAccount(
        ig_user_id=ACCOUNT_IG_ID,
        username="mybrand",
        access_token_enc=encrypt_secret("token"),
        token_expires_at=datetime.now(UTC) + timedelta(days=50),
        webhooks_subscribed=True,
    )
    session.add(acc)
    await session.commit()
    return acc


@pytest.fixture
def ctx(session, account, fake_ig) -> EngineContext:
    return EngineContext(session=session, account=account, client=fake_ig)
