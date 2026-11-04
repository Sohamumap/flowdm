"""Background worker: processes webhook events, delayed flow steps and maintenance.

Run with:  python -m app.jobs.worker

Jobs are processed one at a time, which keeps each contact's flow state consistent
without per-contact locking. Run a single worker process.
"""

import asyncio
import logging
import signal
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import delete, select

from app import alerts
from app.config import get_settings
from app.db import SessionLocal
from app.engine import handlers, next_post
from app.engine.runner import EngineContext
from app.instagram.client import InstagramAPIError, refresh_long_lived_token
from app.instagram.events import parse_webhook
from app.jobs import queue
from app.models import Automation, FlowRun, IGAccount, Job, WebhookEvent
from app.security import decrypt_secret, encrypt_secret

log = logging.getLogger("worker")

TOKEN_REFRESH_BEFORE = timedelta(days=10)
TOKEN_REFRESH_EVERY = timedelta(days=7)


async def _account_context(session, account: IGAccount) -> EngineContext:
    return EngineContext(session=session, account=account, client=handlers.client_factory(account))


async def process_webhook(payload: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        event_row = await session.get(WebhookEvent, payload["webhook_event_id"])
        if event_row is None:
            return
        raw = event_row.payload

    errors: list[str] = []
    for event in parse_webhook(raw):
        async with SessionLocal() as session:
            account = await session.scalar(
                select(IGAccount).where(
                    IGAccount.ig_user_id == event.account_ig_id, IGAccount.is_active.is_(True)
                )
            )
            if account is None:
                log.warning("Webhook for unknown/inactive account %s", event.account_ig_id)
                continue
            ctx = await _account_context(session, account)
            try:
                await handlers.handle_event(ctx, event)
                await session.commit()
            except Exception as exc:
                await session.rollback()
                log.exception("Failed to handle %s", type(event).__name__)
                errors.append(f"{type(event).__name__}: {exc!r}")

    async with SessionLocal() as session:
        event_row = await session.get(WebhookEvent, payload["webhook_event_id"])
        if event_row is not None:
            event_row.processed_at = datetime.now(UTC)
            event_row.error = "\n".join(errors) or None
            await session.commit()
    if errors:
        # Retry: events already handled are skipped thanks to comment_id / mid de-duplication
        raise RuntimeError("; ".join(errors))


async def process_resume_run(payload: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        run = await session.get(FlowRun, payload["run_id"])
        if run is None:
            return
        automation = await session.get(Automation, run.automation_id)
        account = await session.get(IGAccount, automation.account_id) if automation else None
        if account is None or not account.is_active:
            return
        ctx = await _account_context(session, account)
        await handlers.resume_run(ctx, run, payload["step_id"], payload.get("token"))
        await session.commit()


async def process_public_reply(payload: dict[str, Any]) -> None:
    async with SessionLocal() as session:
        account = await session.get(IGAccount, payload["account_id"])
        if account is None or not account.is_active:
            return
        ctx = await _account_context(session, account)
        await handlers.post_public_reply(ctx, payload["comment_event_id"], payload["text"])
        await session.commit()


JOB_HANDLERS = {
    "webhook": process_webhook,
    "resume_run": process_resume_run,
    "public_reply": process_public_reply,
    "email_alert": alerts.process_email_alert,
}


async def run_job(job: Job) -> None:
    handler = JOB_HANDLERS.get(job.type)
    try:
        if handler is None:
            raise RuntimeError(f"Unknown job type {job.type!r}")
        await handler(job.payload)
    except Exception as exc:
        log.warning("Job %s (%s) failed on attempt %s: %r", job.id, job.type, job.attempts, exc)
        async with SessionLocal() as session:
            await queue.mark_failed(session, job, repr(exc))
            await session.commit()
        return
    async with SessionLocal() as session:
        await queue.mark_done(session, job.id)
        await session.commit()


async def run_due_jobs() -> int:
    settings = get_settings()
    async with SessionLocal() as session:
        jobs = await queue.claim_due_jobs(session, settings.worker_batch_size)
        await session.commit()
    for job in jobs:
        await run_job(job)
    return len(jobs)


# ------------------------------------------------------------------ maintenance


async def refresh_tokens() -> None:
    """Long-lived tokens last 60 days; keep them fresh so they never expire."""
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        accounts = list(
            await session.scalars(select(IGAccount).where(IGAccount.is_active.is_(True)))
        )
        for account in accounts:
            last = account.token_refreshed_at or account.created_at
            age = now - last if last else timedelta(days=365)
            expiring = (
                account.token_expires_at is None
                or account.token_expires_at - now < TOKEN_REFRESH_BEFORE
            )
            # Refresh weekly (a pasted token's real expiry is unknown), or sooner if expiring;
            # Instagram only refreshes tokens that are at least 24 hours old.
            if age < timedelta(days=1) or (age < TOKEN_REFRESH_EVERY and not expiring):
                continue
            try:
                data = await refresh_long_lived_token(decrypt_secret(account.access_token_enc))
            except (InstagramAPIError, ValueError, httpx.HTTPError) as exc:
                account.last_error = f"Token refresh failed: {exc}"
                log.warning("Token refresh failed for @%s: %s", account.username, exc)
                await alerts.queue_alert(
                    session,
                    f"token_refresh:{account.id}:{now.date().isoformat()}",
                    f"Instagram access for @{account.username} could not be renewed",
                    f"FlowDM could not renew the Instagram access token for @{account.username}. "
                    "If it expires, comments stop getting DMs.\n\n"
                    f"Error: {exc}\n\n"
                    f"Reconnect the account under Settings: {alerts.dashboard_url('/accounts')}",
                )
                continue
            account.access_token_enc = encrypt_secret(data["access_token"])
            account.token_expires_at = now + timedelta(seconds=int(data.get("expires_in", 5184000)))
            account.token_refreshed_at = now
            account.last_error = None
            log.info("Refreshed token for @%s", account.username)
        await session.commit()


async def maintenance() -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        released = await queue.release_stale_locks(session)
        await queue.prune_finished(session, timedelta(days=3))
        cutoff = datetime.now(UTC) - timedelta(days=settings.webhook_event_retention_days)
        await session.execute(delete(WebhookEvent).where(WebhookEvent.received_at < cutoff))
        await session.commit()
    if released:
        log.warning("Released %s stale job locks", released)
    try:
        await refresh_tokens()
    except Exception:
        log.exception("Token refresh pass crashed")


async def check_new_posts() -> None:
    """Link automations waiting for the next post, for every connected account."""
    async with SessionLocal() as session:
        account_ids = list(
            await session.scalars(select(IGAccount.id).where(IGAccount.is_active.is_(True)))
        )
    for account_id in account_ids:
        async with SessionLocal() as session:
            account = await session.get(IGAccount, account_id)
            if account is None:
                continue
            username = account.username  # read now: a rollback expires the object
            ctx = await _account_context(session, account)
            try:
                await next_post.check_new_posts(ctx)
                await session.commit()
            except (InstagramAPIError, httpx.HTTPError) as exc:
                await session.rollback()
                log.warning("Checking new posts of @%s failed: %s", username, exc)


async def schema_ready() -> bool:
    try:
        async with SessionLocal() as session:
            await session.execute(select(Job.id).limit(1))
        return True
    except Exception:
        return False


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async def pause(seconds: float) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    while not stop.is_set() and not await schema_ready():
        log.info("Waiting for the database and migrations...")
        await pause(3)

    log.info("Worker started")
    next_maintenance = 0.0
    next_post_check = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_maintenance:
            try:
                await maintenance()
                next_maintenance = time.monotonic() + 3600
            except Exception:
                log.exception("Maintenance failed")
                next_maintenance = time.monotonic() + 60
        if time.monotonic() >= next_post_check:
            next_post_check = time.monotonic() + settings.next_post_check_seconds
            try:
                await check_new_posts()
            except Exception:
                log.exception("New post check failed")
        try:
            processed = await run_due_jobs()
        except Exception:
            log.exception("Worker loop error")
            await pause(5)  # e.g. database restarting; don't spin
            continue
        if not processed:
            await pause(settings.worker_poll_seconds)
    log.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
