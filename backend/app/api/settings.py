import re
import smtplib
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator

from app import alerts
from app.api.deps import SessionDep, UserDep
from app.app_settings import load_email_config, load_instagram_config, save_values
from app.config import get_settings

router = APIRouter(tags=["settings"])


class InstagramAppIn(BaseModel):
    """Omit a field (or send null) to keep it; send "" to clear it."""

    instagram_app_id: str | None = None
    instagram_app_secret: str | None = None
    meta_app_secret: str | None = None

    @field_validator("instagram_app_id")
    @classmethod
    def _app_id(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(r"\d{5,30}", value.strip()):
            raise ValueError("The Instagram app ID is a number (digits only)")
        return value

    @field_validator("instagram_app_secret", "meta_app_secret")
    @classmethod
    def _secret(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(r"[A-Za-z0-9]{16,128}", value.strip()):
            raise ValueError("That doesn't look like an app secret (letters and digits only)")
        return value


async def _instagram_app_out(session: SessionDep) -> dict[str, Any]:
    app = await load_instagram_config(session)
    return {
        "instagram_app_id": app.app_id,
        "instagram_app_secret_set": bool(app.app_secret),
        "meta_app_secret_set": bool(app.meta_app_secret),
        "configured": app.configured,
    }


@router.get("/api/config")
async def config(_: UserDep, session: SessionDep) -> dict[str, Any]:
    settings = get_settings()
    base = settings.public_base_url.rstrip("/")
    app = await load_instagram_config(session)
    return {
        "public_base_url": base,
        "webhook_url": f"{base}/webhooks/instagram",
        "webhook_verify_token": settings.webhook_verify_token,
        "oauth_redirect_uri": settings.oauth_redirect_uri,
        "instagram_app_configured": app.configured,
        "graph_api_version": settings.graph_api_version,
    }


@router.get("/api/settings/instagram")
async def get_instagram_app(_: UserDep, session: SessionDep) -> dict[str, Any]:
    return await _instagram_app_out(session)


@router.put("/api/settings/instagram")
async def update_instagram_app(
    body: InstagramAppIn, _: UserDep, session: SessionDep
) -> dict[str, Any]:
    await save_values(session, body.model_dump())
    await session.commit()
    return await _instagram_app_out(session)


EMAIL_RE = r"[^@\s]+@[^@\s]+\.[^@\s]+"


class EmailSettingsIn(BaseModel):
    """Omit a field (or send null) to keep it; send "" to clear it."""

    alert_email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None

    @field_validator("alert_email_to", "smtp_username")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value and not re.fullmatch(EMAIL_RE, value.strip()):
            raise ValueError("That doesn't look like an email address")
        return value

    @field_validator("smtp_port")
    @classmethod
    def _port(cls, value: str | None) -> str | None:
        if value and not (value.strip().isdigit() and 0 < int(value.strip()) < 65536):
            raise ValueError("The port is a number, usually 587")
        return value

    @field_validator("smtp_password")
    @classmethod
    def _password(cls, value: str | None) -> str | None:
        # Google shows app passwords in groups of four ("abcd efgh ijkl mnop")
        return value.replace(" ", "") if value else value


async def _email_out(session: SessionDep) -> dict[str, Any]:
    email = await load_email_config(session)
    return {
        "alert_email_to": email.to,
        "smtp_host": email.host,
        "smtp_port": email.port,
        "smtp_username": email.username,
        "smtp_password_set": bool(email.password),
        "configured": email.configured,
    }


@router.get("/api/settings/email")
async def get_email(_: UserDep, session: SessionDep) -> dict[str, Any]:
    return await _email_out(session)


@router.put("/api/settings/email")
async def update_email(body: EmailSettingsIn, _: UserDep, session: SessionDep) -> dict[str, Any]:
    await save_values(session, body.model_dump())
    await session.commit()
    return await _email_out(session)


@router.post("/api/settings/email/test")
async def test_email(_: UserDep, session: SessionDep) -> dict[str, Any]:
    email = await load_email_config(session)
    if not email.configured:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Fill in the sending address and app password first"
        )
    try:
        await alerts.send_email(
            email,
            "Test email",
            "Email alerts work. FlowDM will write to this address when a DM fails, when a "
            "new post goes live without its automation, or when Instagram access can't be renewed.",
        )
    except (smtplib.SMTPException, OSError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Couldn't send the test email: {exc}"
        ) from exc
    return {"sent_to": email.to}
