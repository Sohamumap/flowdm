from typing import Any

from app.models import Automation, IGAccount
from app.schemas import AutomationIn

FOLLOW_GATE_FLOW: dict[str, Any] = {
    "start_step_id": "open",
    "steps": [
        {
            "id": "open",
            "type": "message",
            "text": "Hey {first_name}! Tap below for the link",
            "buttons": [{"title": "Send me the link", "type": "step", "step_id": "gate"}],
        },
        {
            "id": "gate",
            "type": "follow_check",
            "following_step_id": "link",
            "not_following_step_id": "please_follow",
        },
        {
            "id": "please_follow",
            "type": "message",
            "text": "Please follow first, then tap below",
            "buttons": [{"title": "I followed", "type": "step", "step_id": "gate"}],
        },
        {
            "id": "link",
            "type": "message",
            "text": "Here you go!",
            "buttons": [{"title": "Open link", "type": "url", "url": "https://example.com"}],
        },
    ],
}


def comment_trigger(**overrides: Any) -> dict[str, Any]:
    trigger = {
        "type": "comment",
        "keywords": ["link"],
        "match": "contains",
        "media_ids": [],
        "public_replies": ["Check your DMs!"],
        "once_per_user": True,
        "top_level_only": False,
    }
    trigger.update(overrides)
    return trigger


async def make_automation(
    session,
    account: IGAccount,
    *,
    trigger: dict[str, Any] | None = None,
    flow: dict[str, Any] | None = None,
    name: str = "Test automation",
) -> Automation:
    body = AutomationIn.model_validate(
        {
            "account_id": account.id,
            "name": name,
            "trigger": trigger or comment_trigger(),
            "flow": flow or FOLLOW_GATE_FLOW,
        }
    )
    automation = Automation(
        account_id=account.id,
        name=body.name,
        is_active=True,
        trigger_type=body.trigger.type,
        trigger=body.trigger.model_dump(mode="json"),
        flow=body.flow.model_dump(),
    )
    session.add(automation)
    await session.commit()
    return automation
