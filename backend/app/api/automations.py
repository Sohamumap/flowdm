from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import SessionDep, UserDep, get_account_or_404
from app.models import Automation, CommentEvent, FlowRun
from app.schemas import AutomationIn, AutomationOut

router = APIRouter(prefix="/api/automations", tags=["automations"])


class ActiveIn(BaseModel):
    is_active: bool


async def _get(session: SessionDep, automation_id: int) -> Automation:
    automation = await session.get(Automation, automation_id)
    if automation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Automation not found")
    return automation


def _apply(automation: Automation, body: AutomationIn) -> None:
    automation.account_id = body.account_id
    automation.name = body.name.strip()
    automation.is_active = body.is_active
    automation.trigger_type = body.trigger.type
    automation.trigger = body.trigger.model_dump(mode="json")
    automation.flow = body.flow.model_dump()


@router.get("", response_model=list[AutomationOut])
async def list_automations(
    _: UserDep, session: SessionDep, account_id: int | None = None
) -> list[Automation]:
    query = select(Automation).order_by(Automation.id)
    if account_id is not None:
        query = query.where(Automation.account_id == account_id)
    return list(await session.scalars(query))


@router.post("", response_model=AutomationOut, status_code=status.HTTP_201_CREATED)
async def create_automation(body: AutomationIn, _: UserDep, session: SessionDep) -> Automation:
    await get_account_or_404(session, body.account_id)
    automation = Automation()
    _apply(automation, body)
    session.add(automation)
    await session.commit()
    return automation


@router.get("/{automation_id}", response_model=AutomationOut)
async def get_automation(automation_id: int, _: UserDep, session: SessionDep) -> Automation:
    return await _get(session, automation_id)


@router.put("/{automation_id}", response_model=AutomationOut)
async def update_automation(
    automation_id: int, body: AutomationIn, _: UserDep, session: SessionDep
) -> Automation:
    automation = await _get(session, automation_id)
    await get_account_or_404(session, body.account_id)
    _apply(automation, body)
    await session.commit()
    return automation


@router.patch("/{automation_id}/active", response_model=AutomationOut)
async def set_active(
    automation_id: int, body: ActiveIn, _: UserDep, session: SessionDep
) -> Automation:
    automation = await _get(session, automation_id)
    automation.is_active = body.is_active
    await session.commit()
    return automation


@router.post(
    "/{automation_id}/duplicate", response_model=AutomationOut, status_code=status.HTTP_201_CREATED
)
async def duplicate(automation_id: int, _: UserDep, session: SessionDep) -> Automation:
    source = await _get(session, automation_id)
    copy = Automation(
        account_id=source.account_id,
        name=f"{source.name} (copy)",
        is_active=False,
        trigger_type=source.trigger_type,
        trigger=dict(source.trigger),
        flow=dict(source.flow),
    )
    session.add(copy)
    await session.commit()
    return copy


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(automation_id: int, _: UserDep, session: SessionDep) -> None:
    await session.delete(await _get(session, automation_id))
    await session.commit()


@router.get("/{automation_id}/stats")
async def automation_stats(automation_id: int, _: UserDep, session: SessionDep) -> dict[str, Any]:
    automation = await _get(session, automation_id)
    matched = await session.scalar(
        select(func.count())
        .select_from(CommentEvent)
        .where(CommentEvent.automation_id == automation.id, CommentEvent.status == "matched")
    )
    dms = await session.scalar(
        select(func.count())
        .select_from(CommentEvent)
        .where(CommentEvent.automation_id == automation.id, CommentEvent.dm_sent.is_(True))
    )
    rows = await session.execute(
        select(FlowRun.status, func.count())
        .where(FlowRun.automation_id == automation.id)
        .group_by(FlowRun.status)
    )
    return {
        "triggered": automation.triggered_count,
        "comments_matched": matched or 0,
        "dms_sent": dms or 0,
        "runs": {status_: count for status_, count in rows.all()},
    }
