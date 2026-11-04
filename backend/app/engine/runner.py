"""Executes automation flows step by step for one contact.

Delivery rules that shape this module (Instagram Messaging policy):
  * The first DM to someone who only commented must be a *private reply*
    (recipient = {"comment_id": ...}). Only one private reply is allowed per comment.
  * After that, nothing else can be sent until the person replies or taps a button,
    which opens a 24-hour messaging window. Inside the window we message their IGSID.
So after a private reply the run pauses ("waiting_button" or "waiting_reply") until the
person interacts, then continues normally.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import alerts
from app.instagram.client import InstagramAPIError
from app.jobs.queue import enqueue
from app.models import Automation, CommentEvent, Contact, FlowRun, IGAccount, Message
from app.schemas import (
    CardStep,
    CollectInputStep,
    DelayStep,
    Flow,
    FollowCheckStep,
    MessageStep,
    TagStep,
)

log = logging.getLogger(__name__)

# Slightly under 24h so a message queued at the edge doesn't bounce
MESSAGING_WINDOW = timedelta(hours=23, minutes=50)
MAX_STEPS_PER_ADVANCE = 25
MAX_SEND_RETRIES = 5
PAYLOAD_PREFIX = "flow"

WAITING = ("waiting_button", "waiting_reply", "waiting_input", "waiting_delay", "running")
# Runs that capture the contact's next free-text message
INTERACTIVE = ("waiting_reply", "waiting_input")
TERMINAL = ("completed", "failed", "cancelled")


class MessagingClient(Protocol):
    async def send_message(self, recipient: dict[str, str], message: dict[str, Any]) -> dict: ...
    async def reply_to_comment(self, comment_id: str, text: str) -> dict: ...
    async def get_user_profile(self, igsid: str) -> dict: ...
    async def get_media(self, media_id: str) -> dict: ...
    async def list_media(self, limit: int = 24, after: str | None = None) -> dict: ...


@dataclass
class EngineContext:
    session: AsyncSession
    account: IGAccount
    client: MessagingClient


class OutsideWindowError(Exception):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def window_open(contact: Contact) -> bool:
    return (
        contact.last_inbound_at is not None and now() - contact.last_inbound_at < MESSAGING_WINDOW
    )


def make_payload(automation_id: int, step_id: str) -> str:
    return f"{PAYLOAD_PREFIX}:{automation_id}:{step_id}"


def parse_payload(payload: str | None) -> tuple[int, str] | None:
    if not payload:
        return None
    prefix, _, rest = payload.partition(":")
    automation_id, _, step_id = rest.partition(":")
    if prefix != PAYLOAD_PREFIX or not automation_id.isdigit() or not step_id:
        return None
    return int(automation_id), step_id


def _set_context(run: FlowRun, **values: Any) -> None:
    # Reassign so SQLAlchemy notices the JSONB change
    run.context = {**(run.context or {}), **values}


# ------------------------------------------------------------------ rendering

_PLACEHOLDER = re.compile(r"\{(username|name|first_name|email|phone)\}")


def personalize(text: str, contact: Contact) -> str:
    first_name = (contact.name or "").split(" ")[0] if contact.name else None
    values = {
        "username": contact.username,
        "name": contact.name or contact.username,
        "first_name": first_name or contact.username,
        "email": contact.email,
        "phone": contact.phone,
    }
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1)) or "there", text)


def _render_buttons(step: MessageStep | CardStep, automation_id: int) -> list[dict[str, str]]:
    buttons = []
    for button in step.buttons:
        if button.type == "url":
            buttons.append({"type": "web_url", "url": button.url or "", "title": button.title})
        else:
            buttons.append(
                {
                    "type": "postback",
                    "title": button.title,
                    "payload": make_payload(automation_id, button.step_id or ""),
                }
            )
    return buttons


def render_step(step: MessageStep | CardStep, automation_id: int, contact: Contact) -> dict:
    """Build the Send API `message` object for a message or card step."""
    if isinstance(step, CardStep):
        element: dict[str, Any] = {
            "title": personalize(step.title, contact),
            "image_url": step.image_url,
        }
        if step.subtitle:
            element["subtitle"] = personalize(step.subtitle, contact)
        if step.buttons:
            element["buttons"] = _render_buttons(step, automation_id)
        return {
            "attachment": {
                "type": "template",
                "payload": {"template_type": "generic", "elements": [element]},
            }
        }
    text = personalize(step.text, contact)
    if not step.buttons:
        return {"text": text}
    return {
        "attachment": {
            "type": "template",
            "payload": {
                "template_type": "button",
                "text": text,
                "buttons": _render_buttons(step, automation_id),
            },
        }
    }


def _has_flow_buttons(step: MessageStep | CardStep) -> bool:
    return any(b.type == "step" for b in step.buttons)


# ------------------------------------------------------------------ delivery


async def deliver(
    ctx: EngineContext,
    run: FlowRun | None,
    contact: Contact,
    message: dict[str, Any],
    *,
    kind: str,
    text: str | None,
    automation_id: int | None,
) -> bool:
    """Send one message. Returns True when it went out as a private reply to a comment."""
    comment_id = None
    if run is not None and not (run.context or {}).get("private_reply_used"):
        comment_id = (run.context or {}).get("comment_id")

    if comment_id:
        recipient = {"comment_id": comment_id}
    elif window_open(contact):
        recipient = {"id": contact.igsid}
    else:
        raise OutsideWindowError(
            "The 24-hour messaging window is closed (the contact hasn't messaged recently)"
        )

    record = Message(
        account_id=ctx.account.id,
        contact_id=contact.id,
        direction="out",
        kind="private_reply" if comment_id else kind,
        text=text,
        payload=message,
        automation_id=automation_id,
    )
    try:
        result = await ctx.client.send_message(recipient, message)
    except InstagramAPIError as exc:
        record.error = str(exc)
        ctx.session.add(record)
        raise
    record.mid = result.get("message_id")
    ctx.session.add(record)
    contact.last_interaction_at = now()

    if comment_id and run is not None:
        _set_context(run, private_reply_used=True)
        await ctx.session.execute(
            update(CommentEvent).where(CommentEvent.comment_id == comment_id).values(dm_sent=True)
        )
    return bool(comment_id)


async def refresh_profile(ctx: EngineContext, contact: Contact) -> dict | None:
    """Fetch name/username/follow status. Needs the contact's consent (they messaged us)."""
    profile = await ctx.client.get_user_profile(contact.igsid)
    contact.username = profile.get("username") or contact.username
    contact.name = profile.get("name") or contact.name
    contact.profile_pic = profile.get("profile_pic") or contact.profile_pic
    if "is_user_follow_business" in profile:
        contact.is_follower = bool(profile["is_user_follow_business"])
        contact.follower_checked_at = now()
    return profile


# ------------------------------------------------------------------ input validation

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def validate_input(input_type: str, text: str | None) -> str | None:
    value = (text or "").strip()
    if not value:
        return None
    if input_type == "email":
        value = value.lower()
        return value if EMAIL_RE.match(value) and len(value) <= 320 else None
    if input_type == "phone":
        digits = re.sub(r"[^\d]", "", value)
        if not re.fullmatch(r"[\d\s()+.\-]+", value) or not 7 <= len(digits) <= 15:
            return None
        return ("+" if value.startswith("+") else "") + digits
    return value[:1000]


# ------------------------------------------------------------------ runs


async def load_flow(ctx: EngineContext, run: FlowRun) -> tuple[Automation | None, Flow | None]:
    automation = await ctx.session.get(Automation, run.automation_id)
    if automation is None:
        return None, None
    try:
        return automation, Flow.model_validate(automation.flow)
    except ValueError:
        log.exception("Automation %s has an invalid flow", automation.id)
        return automation, None


async def cancel_interactive_runs(
    ctx: EngineContext, contact: Contact, except_run_id: int | None = None
) -> None:
    query = (
        update(FlowRun)
        .where(FlowRun.contact_id == contact.id, FlowRun.status.in_(INTERACTIVE))
        .values(status="cancelled", updated_at=now())
    )
    if except_run_id is not None:
        query = query.where(FlowRun.id != except_run_id)
    await ctx.session.execute(query)


async def start_run(
    ctx: EngineContext,
    automation: Automation,
    contact: Contact,
    *,
    step_id: str | None = None,
    comment_id: str | None = None,
    count_trigger: bool = True,
) -> FlowRun:
    await cancel_interactive_runs(ctx, contact)
    flow = Flow.model_validate(automation.flow)
    run = FlowRun(
        automation_id=automation.id,
        contact_id=contact.id,
        status="running",
        current_step_id=step_id or flow.start_step_id,
        context={"comment_id": comment_id} if comment_id else {},
    )
    ctx.session.add(run)
    if count_trigger:
        automation.triggered_count = (automation.triggered_count or 0) + 1
    await ctx.session.flush()
    await advance(ctx, run, contact, run.current_step_id)
    return run


async def find_run(
    ctx: EngineContext,
    contact: Contact,
    statuses: tuple[str, ...],
    automation_id: int | None = None,
) -> FlowRun | None:
    query = select(FlowRun).where(FlowRun.contact_id == contact.id, FlowRun.status.in_(statuses))
    if automation_id is not None:
        query = query.where(FlowRun.automation_id == automation_id)
    return await ctx.session.scalar(query.order_by(FlowRun.updated_at.desc()).limit(1))


async def _retry_later(ctx: EngineContext, run: FlowRun, step_id: str, error: str) -> None:
    retries = int((run.context or {}).get("send_retries", 0)) + 1
    if retries > MAX_SEND_RETRIES:
        run.status, run.error = "failed", f"Gave up after {MAX_SEND_RETRIES} retries: {error}"
        return
    token = uuid.uuid4().hex
    _set_context(run, send_retries=retries, resume_token=token)
    run.status, run.current_step_id = "waiting_delay", step_id
    await enqueue(
        ctx.session,
        "resume_run",
        {"run_id": run.id, "step_id": step_id, "token": token},
        delay_seconds=min(30 * 2 ** (retries - 1), 1800),
    )


async def advance(ctx: EngineContext, run: FlowRun, contact: Contact, step_id: str | None) -> None:
    """Run steps from `step_id` until the flow ends or has to wait for something."""
    await _advance(ctx, run, contact, step_id)
    if run.status == "failed":
        await alerts.run_failed(ctx.session, run)


async def _advance(ctx: EngineContext, run: FlowRun, contact: Contact, step_id: str | None) -> None:
    automation, flow = await load_flow(ctx, run)
    if automation is None or flow is None:
        run.status, run.error = "failed", "Automation was deleted or is invalid"
        return
    run.status = "running"

    for _ in range(MAX_STEPS_PER_ADVANCE):
        step = flow.get(step_id)
        if step is None:
            run.status, run.current_step_id = "completed", None
            return
        run.current_step_id = step.id
        run.steps_executed = (run.steps_executed or 0) + 1
        try:
            next_id = await _execute_step(ctx, run, contact, automation, step)
        except OutsideWindowError as exc:
            run.status, run.error = "failed", str(exc)
            return
        except (InstagramAPIError, httpx.TransportError) as exc:
            retryable = not isinstance(exc, InstagramAPIError) or exc.is_retryable
            if retryable:
                await _retry_later(ctx, run, step.id, str(exc))
            else:
                run.status, run.error = "failed", str(exc)
                if isinstance(exc, InstagramAPIError) and exc.is_token_error:
                    ctx.account.last_error = f"Access token rejected: {exc.message}"
            return
        if run.status != "running":
            return  # the step made the run wait
        step_id = next_id

    run.status, run.error = "failed", "Too many steps in a row - does the flow loop on itself?"


async def _execute_step(
    ctx: EngineContext, run: FlowRun, contact: Contact, automation: Automation, step: Any
) -> str | None:
    """Execute one step. Returns the next step id, or sets run.status to a waiting state."""
    if isinstance(step, MessageStep | CardStep):
        message = render_step(step, automation.id, contact)
        text = step.text if isinstance(step, MessageStep) else step.title
        kind = "template" if step.buttons or isinstance(step, CardStep) else "text"
        via_private_reply = await deliver(
            ctx, run, contact, message, kind=kind, text=text, automation_id=automation.id
        )
        _set_context(run, send_retries=0)
        if _has_flow_buttons(step):
            run.status = "waiting_button"
            return None
        if step.next_step_id and via_private_reply and not window_open(contact):
            # Instagram allows nothing else until the person replies to the private reply
            run.status, run.current_step_id = "waiting_reply", step.next_step_id
            return None
        return step.next_step_id

    if isinstance(step, DelayStep):
        if not step.next_step_id:
            return None
        token = uuid.uuid4().hex
        _set_context(run, resume_token=token)
        run.status, run.current_step_id = "waiting_delay", step.next_step_id
        await enqueue(
            ctx.session,
            "resume_run",
            {"run_id": run.id, "step_id": step.next_step_id, "token": token},
            delay_seconds=step.seconds,
        )
        return None

    if isinstance(step, FollowCheckStep):
        try:
            await refresh_profile(ctx, contact)
        except InstagramAPIError as exc:
            if exc.is_retryable:
                raise
            # Usually "user consent is required": treat as not following
            log.info("Follow check failed for contact %s: %s", contact.id, exc)
            contact.is_follower = None
        follows = bool(contact.is_follower)
        return step.following_step_id if follows else step.not_following_step_id

    if isinstance(step, CollectInputStep):
        message: dict[str, Any] = {"text": personalize(step.text, contact)}
        if step.input_type == "email":
            message["quick_replies"] = [{"content_type": "user_email"}]
        elif step.input_type == "phone":
            message["quick_replies"] = [{"content_type": "user_phone_number"}]
        await deliver(
            ctx, run, contact, message, kind="text", text=step.text, automation_id=automation.id
        )
        _set_context(run, input_attempts=0, send_retries=0)
        run.status = "waiting_input"
        return None

    if isinstance(step, TagStep):
        contact.tags = sorted(set(contact.tags or []) | {t.strip() for t in step.tags if t.strip()})
        return step.next_step_id

    log.warning("Unknown step type %r", getattr(step, "type", step))
    return None


async def handle_input(
    ctx: EngineContext, run: FlowRun, contact: Contact, text: str | None
) -> bool:
    """Feed a reply into a run that is waiting for input. False = not consumed."""
    automation, flow = await load_flow(ctx, run)
    step = flow.get(run.current_step_id) if flow else None
    if automation is None or not isinstance(step, CollectInputStep):
        run.status = "cancelled"
        return False

    value = validate_input(step.input_type, text)
    if value is None:
        attempts = int((run.context or {}).get("input_attempts", 0)) + 1
        if attempts >= step.max_attempts:
            run.status, run.error = "cancelled", "Too many invalid answers"
            return False
        _set_context(run, input_attempts=attempts)
        try:
            await deliver(
                ctx,
                run,
                contact,
                {"text": personalize(step.retry_text, contact)},
                kind="text",
                text=step.retry_text,
                automation_id=automation.id,
            )
        except (InstagramAPIError, OutsideWindowError, httpx.TransportError) as exc:
            log.warning("Could not send retry prompt: %s", exc)
        return True

    if step.input_type == "email":
        contact.email = value
    elif step.input_type == "phone":
        contact.phone = value
    else:
        contact.custom_fields = {
            **(contact.custom_fields or {}),
            step.field_name or "answer": value,
        }
    _set_context(run, input_attempts=0)
    await advance(ctx, run, contact, step.next_step_id)
    return True
