"""Automations that wait for the next post and link themselves to it once it is live.

Instagram gives a scheduled post its id only when it goes live, so an automation made ahead
of time can't name its post. Instead it waits with a caption word (CommentTrigger.next_post_word).
The worker checks the account's newest posts every couple of minutes, and the first comment on
an unknown post triggers the same check, so the automation is linked before that comment is
matched. Posts published more than LINK_GRACE before the wait started are never linked; the
grace covers setting up the automation just after the post went live.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import exists, select

from app import alerts
from app.engine.matcher import matches_keywords
from app.engine.runner import EngineContext
from app.instagram.client import InstagramAPIError
from app.models import Automation
from app.schemas import CommentTrigger

log = logging.getLogger(__name__)

RECENT_POSTS = 10
LINK_GRACE = timedelta(minutes=10)


def _too_old(published: datetime, waiting_since: datetime | None) -> bool:
    return waiting_since is not None and published < waiting_since - LINK_GRACE


def parse_timestamp(value: str | None) -> datetime | None:
    """Instagram timestamps look like 2026-11-05T13:30:00+0000."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


async def waiting_automations(ctx: EngineContext) -> list[tuple[Automation, CommentTrigger]]:
    rows = await ctx.session.scalars(
        select(Automation)
        .where(
            Automation.account_id == ctx.account.id,
            Automation.is_active.is_(True),
            Automation.trigger_type == "comment",
        )
        .order_by(Automation.id)
    )
    waiting = []
    for automation in rows:
        try:
            trigger = CommentTrigger.model_validate(automation.trigger)
        except ValueError:
            continue
        if trigger.waiting_for_post:
            waiting.append((automation, trigger))
    return waiting


async def is_linked(ctx: EngineContext, media_id: str) -> bool:
    return bool(
        await ctx.session.scalar(
            select(
                exists().where(
                    Automation.account_id == ctx.account.id,
                    Automation.trigger.contains({"media_ids": [media_id]}),
                )
            )
        )
    )


async def link_post(ctx: EngineContext, media: dict[str, Any]) -> list[Automation]:
    """Link every waiting automation whose word is in this post's caption."""
    published = parse_timestamp(media.get("timestamp"))
    if published is None or not media.get("id"):
        return []
    linked = []
    for automation, trigger in await waiting_automations(ctx):
        if _too_old(published, trigger.waiting_since):
            continue
        if not matches_keywords(media.get("caption") or "", [trigger.next_post_word], "contains"):
            continue
        trigger.media_ids = [media["id"]]
        trigger.next_post_word = ""
        trigger.waiting_since = None
        automation.trigger = trigger.model_dump(mode="json")
        linked.append(automation)
        log.info("Linked automation %s to new post %s", automation.id, media["id"])
    if linked:
        await ctx.session.flush()
    return linked


async def link_from_comment(ctx: EngineContext, media_id: str | None) -> None:
    """A comment arrived on a post no automation knows yet: maybe it's the awaited post."""
    if not media_id or await is_linked(ctx, media_id) or not await waiting_automations(ctx):
        return
    try:
        media = await ctx.client.get_media(media_id)
    except (InstagramAPIError, httpx.HTTPError) as exc:
        log.warning("Could not load post %s to check waiting automations: %s", media_id, exc)
        return
    await link_post(ctx, media)


async def check_new_posts(ctx: EngineContext) -> None:
    waiting = await waiting_automations(ctx)
    if not waiting:
        return
    since = min((t.waiting_since for _, t in waiting if t.waiting_since), default=None)
    data = await ctx.client.list_media(limit=RECENT_POSTS)
    for media in data.get("data") or []:
        published = parse_timestamp(media.get("timestamp"))
        if published is None or not media.get("id") or _too_old(published, since):
            continue
        if await link_post(ctx, media) or await is_linked(ctx, media["id"]):
            continue
        still_waiting = await waiting_automations(ctx)
        if not still_waiting:
            return
        words = ", ".join(f'"{t.next_post_word}"' for _, t in still_waiting)
        caption = (media.get("caption") or "").strip().replace("\n", " ")
        await alerts.queue_alert(
            ctx.session,
            f"unmatched_post:{media['id']}",
            "A new post went live without a DM",
            "A new post went live, but its caption has none of the words your waiting "
            f"automations look for ({words}), so no automation was linked to it.\n\n"
            f"Post: {media.get('permalink') or media['id']}\n"
            f"Caption: {caption[:200] or '(no caption)'}\n\n"
            "If this post should send a DM, open the automation in FlowDM and pick the post "
            f"under Specific posts: {alerts.dashboard_url('/automations')}",
        )
