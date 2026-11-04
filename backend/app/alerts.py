"""Email alerts for problems that need the owner, so nobody has to watch the dashboard.

Alerts are queued as "email_alert" jobs in the same transaction as the event that caused
them, and de-duplicated by a key (for example one per automation per hour), so a burst of
failures sends one email. Sent keys are kept in the app_settings table for a week.
"""

import asyncio
import json
import logging
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.app_settings import EmailConfig, load_email_config
from app.config import get_settings
from app.db import SessionLocal
from app.jobs.queue import enqueue
from app.models import AppSetting, Automation, Contact, FlowRun

log = logging.getLogger(__name__)

SENT_KEY = "alerts_sent"
KEEP_SENT = timedelta(days=7)
SMTP_TIMEOUT = 20


def dashboard_url(path: str = "") -> str:
    return get_settings().public_base_url.rstrip("/") + path


async def queue_alert(session: AsyncSession, key: str, subject: str, body: str) -> bool:
    """Queue an email unless one with this key went out in the last week. True if queued."""
    now = datetime.now(UTC)
    row = await session.get(AppSetting, SENT_KEY)
    try:
        sent: dict[str, str] = json.loads(row.value) if row else {}
    except ValueError:
        sent = {}
    sent = {k: v for k, v in sent.items() if datetime.fromisoformat(v) > now - KEEP_SENT}
    if key in sent:
        return False
    sent[key] = now.isoformat()
    if row is None:
        session.add(AppSetting(key=SENT_KEY, value=json.dumps(sent)))
    else:
        row.value = json.dumps(sent)
    await enqueue(session, "email_alert", {"subject": subject, "body": body})
    log.info("Queued alert %s", key)
    return True


async def run_failed(session: AsyncSession, run: FlowRun) -> None:
    automation = await session.get(Automation, run.automation_id)
    contact = await session.get(Contact, run.contact_id)
    name = automation.name if automation else f"automation {run.automation_id}"
    who = f"@{contact.username}" if contact and contact.username else "someone"
    hour = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    await queue_alert(
        session,
        f"run_failed:{run.automation_id}:{hour}",
        f'A DM to {who} failed in "{name}"',
        f'FlowDM could not finish "{name}" for {who}.\n\n'
        f"Error: {run.error or 'unknown'}\n\n"
        "Other failures in this automation during the next hour are not emailed. "
        f"See them all under Activity: {dashboard_url('/activity')}",
    )


def _send_sync(config: EmailConfig, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = f"FlowDM: {subject}"
    message["From"] = config.username
    message["To"] = config.to
    message.set_content(body)
    if config.port == 465:
        with smtplib.SMTP_SSL(config.host, config.port, timeout=SMTP_TIMEOUT) as smtp:
            smtp.login(config.username, config.password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(config.host, config.port, timeout=SMTP_TIMEOUT) as smtp:
            smtp.starttls()
            smtp.login(config.username, config.password)
            smtp.send_message(message)


async def send_email(config: EmailConfig, subject: str, body: str) -> None:
    await asyncio.to_thread(_send_sync, config, subject, body)


async def process_email_alert(payload: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        config = await load_email_config(session)
    if not config.configured:
        log.warning("Alert not emailed (email alerts are not set up): %s", payload["subject"])
        return
    await send_email(config, payload["subject"], payload["body"])
