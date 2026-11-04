"""Pydantic models for automations (trigger + flow) and API payloads."""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Instagram limits
MAX_BUTTONS = 3
BUTTON_TITLE_MAX = 20
TEXT_MAX = 1000
TEMPLATE_TEXT_MAX = 640
CARD_TITLE_MAX = 80
CARD_SUBTITLE_MAX = 80


# ---------------------------------------------------------------- triggers


class CommentTrigger(BaseModel):
    type: Literal["comment"] = "comment"
    keywords: list[str] = Field(default_factory=list)
    match: Literal["any", "contains", "exact"] = "contains"
    # Empty list = every post/reel; otherwise only these media ids
    media_ids: list[str] = Field(default_factory=list)
    # Public replies posted under the comment; one is picked at random each time
    public_replies: list[str] = Field(default_factory=list)
    # Only DM each person once per automation, even if they comment again
    once_per_user: bool = True
    # Ignore replies to other comments (only top-level comments trigger)
    top_level_only: bool = False
    # Wait for the next post: with no media_ids, the automation stays idle until a post whose
    # caption contains this word goes live after waiting_since, then links itself to that post.
    # Instagram only gives a scheduled post its id once it is live, so it can't be picked earlier.
    next_post_word: str = ""
    waiting_since: datetime | None = None

    @model_validator(mode="after")
    def _check(self) -> "CommentTrigger":
        self.keywords = [k.strip() for k in self.keywords if k.strip()]
        self.public_replies = [r.strip() for r in self.public_replies if r.strip()]
        self.next_post_word = self.next_post_word.strip()
        if self.match != "any" and not self.keywords:
            raise ValueError("Add at least one keyword, or set match to 'any'")
        if self.media_ids:
            self.next_post_word = ""
        if not self.next_post_word:
            self.waiting_since = None
        elif self.waiting_since is None:
            self.waiting_since = datetime.now(UTC)
        return self

    @property
    def waiting_for_post(self) -> bool:
        return bool(self.next_post_word) and not self.media_ids


class DMKeywordTrigger(BaseModel):
    type: Literal["dm_keyword"] = "dm_keyword"
    keywords: list[str] = Field(default_factory=list)
    match: Literal["contains", "exact"] = "contains"

    @model_validator(mode="after")
    def _check(self) -> "DMKeywordTrigger":
        self.keywords = [k.strip() for k in self.keywords if k.strip()]
        if not self.keywords:
            raise ValueError("Add at least one keyword")
        return self


Trigger = Annotated[CommentTrigger | DMKeywordTrigger, Field(discriminator="type")]


# ---------------------------------------------------------------- flow steps


class Button(BaseModel):
    title: str = Field(min_length=1, max_length=BUTTON_TITLE_MAX)
    # "step": tapping continues the flow at step_id; "url": opens a link
    type: Literal["step", "url"]
    step_id: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Button":
        if self.type == "step" and not self.step_id:
            raise ValueError(f"Button '{self.title}' must point to a step")
        if self.type == "url" and not (self.url or "").startswith(("http://", "https://")):
            raise ValueError(f"Button '{self.title}' needs a link starting with https://")
        return self


class StepBase(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = ""


class MessageStep(StepBase):
    """A text message, optionally with up to 3 buttons."""

    type: Literal["message"] = "message"
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    buttons: list[Button] = Field(default_factory=list, max_length=MAX_BUTTONS)
    next_step_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "MessageStep":
        # Instagram counts UTF-8 bytes, so emoji and non-Latin scripts use up the limit faster
        limit = TEMPLATE_TEXT_MAX if self.buttons else TEXT_MAX
        if len(self.text.encode()) > limit:
            raise ValueError(f"Message is too long (Instagram allows {limit} bytes)")
        return self


class CardStep(StepBase):
    """Image card with title, subtitle and up to 3 buttons (Instagram generic template)."""

    type: Literal["card"] = "card"
    image_url: str
    title: str = Field(min_length=1, max_length=CARD_TITLE_MAX)
    subtitle: str = Field(default="", max_length=CARD_SUBTITLE_MAX)
    buttons: list[Button] = Field(default_factory=list, max_length=MAX_BUTTONS)
    next_step_id: str | None = None

    @field_validator("image_url")
    @classmethod
    def _url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("Image URL must start with https://")
        return value


class DelayStep(StepBase):
    type: Literal["delay"] = "delay"
    seconds: int = Field(ge=1, le=60 * 60 * 23)
    next_step_id: str | None = None


class FollowCheckStep(StepBase):
    """Branch on whether the contact follows the account (the 'follow to unlock' gate)."""

    type: Literal["follow_check"] = "follow_check"
    following_step_id: str | None = None
    not_following_step_id: str | None = None


class CollectInputStep(StepBase):
    """Ask a question and save the next reply on the contact."""

    type: Literal["collect_input"] = "collect_input"
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    input_type: Literal["email", "phone", "text"] = "email"
    # Custom field name when input_type is "text" (email/phone go to their own columns)
    field_name: str | None = None
    retry_text: str = "Hmm, that doesn't look right. Please try again."
    max_attempts: int = Field(default=3, ge=1, le=10)
    next_step_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "CollectInputStep":
        if self.input_type == "text" and not (self.field_name or "").strip():
            raise ValueError("Give the answer a field name to save it under")
        return self


class TagStep(StepBase):
    """Add tags to the contact (handy for segmenting leads)."""

    type: Literal["add_tag"] = "add_tag"
    tags: list[str] = Field(min_length=1)
    next_step_id: str | None = None


Step = Annotated[
    MessageStep | CardStep | DelayStep | FollowCheckStep | CollectInputStep | TagStep,
    Field(discriminator="type"),
]


def step_targets(step: Any) -> list[str]:
    """Every step id a step can lead to."""
    targets: list[str | None] = []
    if isinstance(step, MessageStep | CardStep):
        targets += [b.step_id for b in step.buttons if b.type == "step"]
    if isinstance(step, FollowCheckStep):
        targets += [step.following_step_id, step.not_following_step_id]
    else:
        targets.append(getattr(step, "next_step_id", None))
    return [t for t in targets if t]


class Flow(BaseModel):
    start_step_id: str
    steps: list[Step] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _check(self) -> "Flow":
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Step ids must be unique")
        known = set(ids)
        if self.start_step_id not in known:
            raise ValueError("Start step does not exist")
        for step in self.steps:
            for target in step_targets(step):
                if target not in known:
                    raise ValueError(f"Step '{step.label or step.id}' points to a missing step")
        return self

    def get(self, step_id: str | None) -> Any:
        return next((s for s in self.steps if s.id == step_id), None)


# ---------------------------------------------------------------- API models


class AutomationIn(BaseModel):
    account_id: int
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True
    trigger: Trigger
    flow: Flow

    @model_validator(mode="after")
    def _check(self) -> "AutomationIn":
        first = self.flow.get(self.flow.start_step_id)
        if self.trigger.type == "comment" and not isinstance(first, MessageStep | CardStep):
            raise ValueError(
                "For comment automations the first step must be a message or card "
                "(it is sent as the private reply to the comment)"
            )
        return self


class AutomationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    name: str
    is_active: bool
    trigger_type: str
    trigger: dict[str, Any]
    flow: dict[str, Any]
    triggered_count: int
    created_at: datetime
    updated_at: datetime


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ig_user_id: str
    username: str
    name: str | None
    profile_picture_url: str | None
    token_expires_at: datetime | None
    token_refreshed_at: datetime | None
    webhooks_subscribed: bool
    is_active: bool
    last_error: str | None
    created_at: datetime


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    igsid: str
    username: str | None
    name: str | None
    profile_pic: str | None
    is_follower: bool | None
    email: str | None
    phone: str | None
    custom_fields: dict[str, Any]
    tags: list[str]
    last_inbound_at: datetime | None
    last_interaction_at: datetime | None
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    contact_id: int | None
    direction: str
    kind: str
    text: str | None
    payload: dict[str, Any] | None
    automation_id: int | None
    error: str | None
    created_at: datetime


class CommentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    comment_id: str
    media_id: str | None
    text: str
    from_username: str | None
    contact_id: int | None
    automation_id: int | None
    status: str
    dm_sent: bool
    public_reply_id: str | None
    error: str | None
    created_at: datetime


class FlowRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    automation_id: int
    contact_id: int
    status: str
    current_step_id: str | None
    steps_executed: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class Page[T](BaseModel):
    items: list[T]
    total: int
