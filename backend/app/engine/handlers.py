"""Turn incoming Instagram events into automation runs."""

import logging
import random
from collections.abc import Callable

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from app.engine import next_post
from app.engine.matcher import matches_keywords
from app.engine.runner import (
    TERMINAL,
    EngineContext,
    MessagingClient,
    advance,
    cancel_interactive_runs,
    find_run,
    handle_input,
    now,
    parse_payload,
    personalize,
    refresh_profile,
    start_run,
)
from app.instagram.client import InstagramAPIError, InstagramClient
from app.instagram.events import CommentEvent, InstagramEvent, MessageEvent, PostbackEvent
from app.jobs.queue import enqueue
from app.models import Automation, Contact, FlowRun, IGAccount, Message
from app.models import CommentEvent as CommentEventRow
from app.schemas import CommentTrigger, DMKeywordTrigger
from app.security import decrypt_secret

log = logging.getLogger(__name__)

ClientFactory = Callable[[IGAccount], MessagingClient]


def default_client_factory(account: IGAccount) -> MessagingClient:
    return InstagramClient(decrypt_secret(account.access_token_enc), account.ig_user_id)


# Tests swap this out for a fake client
client_factory: ClientFactory = default_client_factory


async def upsert_contact(
    ctx: EngineContext, igsid: str, username: str | None = None
) -> tuple[Contact, bool]:
    contact = await ctx.session.scalar(
        select(Contact).where(Contact.account_id == ctx.account.id, Contact.igsid == igsid)
    )
    created = contact is None
    if contact is None:
        contact = Contact(account_id=ctx.account.id, igsid=igsid, custom_fields={}, tags=[])
        ctx.session.add(contact)
    if username:
        contact.username = username
    contact.last_interaction_at = now()
    await ctx.session.flush()
    return contact, created


async def _already_seen(ctx: EngineContext, mid: str | None) -> bool:
    if not mid:
        return False
    return bool(await ctx.session.scalar(select(exists().where(Message.mid == mid))))


async def _active_automations(ctx: EngineContext, trigger_type: str) -> list[Automation]:
    rows = await ctx.session.scalars(
        select(Automation)
        .where(
            Automation.account_id == ctx.account.id,
            Automation.is_active.is_(True),
            Automation.trigger_type == trigger_type,
        )
        .order_by(Automation.id)
    )
    return list(rows)


async def handle_event(ctx: EngineContext, event: InstagramEvent) -> None:
    if isinstance(event, CommentEvent):
        await handle_comment(ctx, event)
    elif isinstance(event, PostbackEvent):
        await handle_postback(ctx, event)
    elif isinstance(event, MessageEvent):
        await handle_message(ctx, event)


# ------------------------------------------------------------------ comments


def comment_automation_matches(trigger: CommentTrigger, event: CommentEvent) -> bool:
    if trigger.waiting_for_post:
        return False  # not linked to its post yet; it must never fire on older posts
    if trigger.media_ids and event.media_id not in trigger.media_ids:
        return False
    if trigger.top_level_only and event.parent_id:
        return False
    return matches_keywords(event.text, trigger.keywords, trigger.match)


async def handle_comment(ctx: EngineContext, event: CommentEvent) -> None:
    if event.from_id == ctx.account.ig_user_id:
        return  # our own comment / public reply
    if await ctx.session.scalar(
        select(exists().where(CommentEventRow.comment_id == event.comment_id))
    ):
        return  # duplicate delivery

    contact, _ = await upsert_contact(ctx, event.from_id, event.from_username)
    row = CommentEventRow(
        account_id=ctx.account.id,
        comment_id=event.comment_id,
        media_id=event.media_id,
        parent_id=event.parent_id,
        text=event.text,
        from_igsid=event.from_id,
        from_username=event.from_username,
        contact_id=contact.id,
        status="no_match",
    )
    ctx.session.add(row)
    try:
        await ctx.session.flush()
    except IntegrityError:
        return  # raced with a duplicate delivery

    await next_post.link_from_comment(ctx, event.media_id)
    for automation in await _active_automations(ctx, "comment"):
        try:
            trigger = CommentTrigger.model_validate(automation.trigger)
        except ValueError:
            log.warning("Automation %s has an invalid trigger", automation.id)
            continue
        if not comment_automation_matches(trigger, event):
            continue

        row.automation_id = automation.id
        if trigger.once_per_user and await ctx.session.scalar(
            select(
                exists().where(
                    CommentEventRow.automation_id == automation.id,
                    CommentEventRow.contact_id == contact.id,
                    CommentEventRow.status == "matched",
                    CommentEventRow.error.is_(None),  # a failed DM doesn't count
                    CommentEventRow.id != row.id,
                )
            )
        ):
            row.status = "skipped_repeat"
            return

        row.status = "matched"
        run = await start_run(ctx, automation, contact, comment_id=event.comment_id)
        if run.status == "failed":
            row.error = run.error

        # Instagram only allows public replies to top-level comments
        if trigger.public_replies and not event.parent_id:
            await enqueue(
                ctx.session,
                "public_reply",
                {
                    "account_id": ctx.account.id,
                    "comment_event_id": row.id,
                    "text": personalize(random.choice(trigger.public_replies), contact),
                },
            )
        return


# ------------------------------------------------------------------ DMs and buttons


async def _touch_inbound(ctx: EngineContext, contact: Contact, created: bool) -> None:
    contact.last_inbound_at = now()
    # Messaging us grants consent to read their profile; load it until it has succeeded once
    if created or not contact.username or contact.follower_checked_at is None:
        try:
            await refresh_profile(ctx, contact)
        except Exception as exc:  # profile is nice-to-have; never block the flow
            log.info("Could not load profile for %s: %s", contact.igsid, exc)


async def handle_postback(ctx: EngineContext, event: PostbackEvent) -> None:
    if event.sender_id == ctx.account.ig_user_id or await _already_seen(ctx, event.mid):
        return
    contact, created = await upsert_contact(ctx, event.sender_id)
    await _touch_inbound(ctx, contact, created)
    ctx.session.add(
        Message(
            account_id=ctx.account.id,
            contact_id=contact.id,
            direction="in",
            mid=event.mid,
            kind="postback",
            text=event.title,
            payload={"payload": event.payload},
        )
    )
    await continue_from_payload(ctx, contact, event.payload)


async def continue_from_payload(ctx: EngineContext, contact: Contact, payload: str | None) -> bool:
    parsed = parse_payload(payload)
    if parsed is None:
        return False
    automation_id, step_id = parsed
    automation = await ctx.session.get(Automation, automation_id)
    if automation is None or automation.account_id != ctx.account.id or not automation.is_active:
        return False

    run = await find_run(
        ctx,
        contact,
        ("waiting_button", "waiting_reply", "waiting_input", "running"),
        automation_id=automation.id,
    )
    if run is None or run.status in TERMINAL:
        await start_run(ctx, automation, contact, step_id=step_id, count_trigger=False)
        return True
    await cancel_interactive_runs(ctx, contact, except_run_id=run.id)
    await advance(ctx, run, contact, step_id)
    return True


async def handle_message(ctx: EngineContext, event: MessageEvent) -> None:
    if event.is_echo or event.sender_id == ctx.account.ig_user_id:
        return
    if await _already_seen(ctx, event.mid):
        return
    contact, created = await upsert_contact(ctx, event.sender_id)
    await _touch_inbound(ctx, contact, created)
    ctx.session.add(
        Message(
            account_id=ctx.account.id,
            contact_id=contact.id,
            direction="in",
            mid=event.mid,
            kind="quick_reply"
            if event.quick_reply_payload
            else ("text" if event.text else "attachment"),
            text=event.text,
            payload={"attachments": event.attachments} if event.attachments else None,
        )
    )
    await ctx.session.flush()

    # 1. Quick reply buttons that belong to a flow
    if await continue_from_payload(ctx, contact, event.quick_reply_payload):
        return

    # 2. A flow is waiting for this person's answer
    run = await find_run(ctx, contact, ("waiting_input",))
    if run is not None and await handle_input(ctx, run, contact, event.text):
        return

    # 3. A flow paused after a private reply, waiting for them to reply
    run = await find_run(ctx, contact, ("waiting_reply",))
    if run is not None:
        await advance(ctx, run, contact, run.current_step_id)
        return

    # 4. DM keyword automations
    if not event.text:
        return
    for automation in await _active_automations(ctx, "dm_keyword"):
        try:
            trigger = DMKeywordTrigger.model_validate(automation.trigger)
        except ValueError:
            continue
        if matches_keywords(event.text, trigger.keywords, trigger.match):
            await start_run(ctx, automation, contact)
            return


# ------------------------------------------------------------------ jobs


async def resume_run(ctx: EngineContext, run: FlowRun, step_id: str, token: str | None) -> None:
    if run.status != "waiting_delay" or (run.context or {}).get("resume_token") != token:
        return  # run moved on (button tapped, cancelled, ...) since the job was scheduled
    contact = await ctx.session.get(Contact, run.contact_id)
    if contact is None:
        return
    await advance(ctx, run, contact, step_id)


async def post_public_reply(ctx: EngineContext, comment_event_id: int, text: str) -> None:
    row = await ctx.session.get(CommentEventRow, comment_event_id)
    if row is None or row.public_reply_id:
        return
    try:
        result = await ctx.client.reply_to_comment(row.comment_id, text)
    except InstagramAPIError as exc:
        if exc.is_retryable:
            raise
        row.error = f"Public reply failed: {exc}"
        return
    row.public_reply_id = result.get("id")
