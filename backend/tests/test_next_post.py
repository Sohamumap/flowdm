from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import alerts
from app.app_settings import save_values
from app.engine import handlers, next_post
from app.models import Automation, Job
from app.models import CommentEvent as CommentRow
from app.schemas import CommentTrigger
from tests.conftest import api_error
from tests.factories import comment_trigger, make_automation
from tests.test_engine import comment


def ig_time(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+0000")


def post(media_id: str, caption: str, minutes_ago: float = 1) -> dict:
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return {
        "id": media_id,
        "caption": caption,
        "permalink": f"https://www.instagram.com/reel/{media_id}/",
        "timestamp": ig_time(when),
    }


async def waiting_automation(session, account, word="clone", **trigger):
    return await make_automation(
        session,
        account,
        trigger=comment_trigger(keywords=["clone", "link"], next_post_word=word, **trigger),
    )


async def alert_jobs(session) -> list[Job]:
    return list(await session.scalars(select(Job).where(Job.type == "email_alert")))


async def test_waiting_automation_never_fires_on_older_posts(ctx, session, account, fake_ig):
    automation = await waiting_automation(session, account)
    fake_ig.media = [post("old_reel", "Comment CLONE for the repo", minutes_ago=600)]

    await handlers.handle_event(ctx, comment("clone", media_id="old_reel"))
    await session.commit()

    assert fake_ig.sent == []
    row = await session.scalar(select(CommentRow))
    assert row.status == "no_match"
    await session.refresh(automation)
    assert automation.trigger["media_ids"] == []
    assert automation.trigger["next_post_word"] == "clone"


async def test_first_comment_on_new_post_links_and_gets_the_dm(ctx, session, account, fake_ig):
    automation = await waiting_automation(session, account)
    fake_ig.media = [post("new_reel", "Replica is out. Comment CLONE and I'll DM you the link")]

    await handlers.handle_event(ctx, comment("CLONE", media_id="new_reel"))
    await session.commit()

    await session.refresh(automation)
    assert automation.trigger["media_ids"] == ["new_reel"]
    assert automation.trigger["next_post_word"] == ""
    assert automation.trigger["waiting_since"] is None
    assert len(fake_ig.sent) == 1
    row = await session.scalar(select(CommentRow))
    assert (row.status, row.automation_id) == ("matched", automation.id)

    # Now linked: a later comment on another post doesn't fire, and no extra lookups happen
    calls = fake_ig.media_calls
    await handlers.handle_event(ctx, comment("clone", comment_id="c2", media_id="other"))
    await session.commit()
    assert len(fake_ig.sent) == 1
    assert fake_ig.media_calls == calls


async def test_caption_without_the_word_is_not_linked(ctx, session, account, fake_ig):
    automation = await waiting_automation(session, account)
    fake_ig.media = [post("new_reel", "Link in bio for the full guide")]

    await handlers.handle_event(ctx, comment("link", media_id="new_reel"))
    await session.commit()

    await session.refresh(automation)
    assert automation.trigger["media_ids"] == []
    assert fake_ig.sent == []


async def test_word_matches_whole_words_only(ctx, session, account, fake_ig):
    automation = await waiting_automation(session, account)
    fake_ig.media = [post("new_reel", "My cloned voice, part 2")]

    await next_post.check_new_posts(ctx)
    await session.commit()

    await session.refresh(automation)
    assert automation.trigger["media_ids"] == []


async def test_poll_links_new_post_before_any_comment(ctx, session, account, fake_ig):
    clone = await waiting_automation(session, account)
    nokia = await waiting_automation(session, account, word="nokia")
    fake_ig.media = [
        post("new_reel", "Comment #CLONE to get it", minutes_ago=1),
        post("old_reel", "Comment NOKIA", minutes_ago=600),
    ]

    await next_post.check_new_posts(ctx)
    await session.commit()

    await session.refresh(clone)
    await session.refresh(nokia)
    assert clone.trigger["media_ids"] == ["new_reel"]
    assert nokia.trigger["media_ids"] == []  # its word is only on a post older than the wait
    assert await alert_jobs(session) == []


async def test_paused_automation_does_not_link(ctx, session, account, fake_ig):
    automation = await waiting_automation(session, account)
    automation.is_active = False
    await session.commit()
    fake_ig.media = [post("new_reel", "Comment CLONE")]

    await next_post.check_new_posts(ctx)
    await session.commit()

    await session.refresh(automation)
    assert automation.trigger["media_ids"] == []
    assert fake_ig.media_calls == 0  # nothing is waiting, so Instagram isn't asked


async def test_new_post_without_the_word_sends_one_alert(ctx, session, account, fake_ig):
    await waiting_automation(session, account)
    fake_ig.media = [post("new_reel", "Weekend vlog, no DM today")]

    await next_post.check_new_posts(ctx)
    await next_post.check_new_posts(ctx)
    await session.commit()

    jobs = await alert_jobs(session)
    assert len(jobs) == 1
    assert '"clone"' in jobs[0].payload["body"]
    assert "https://www.instagram.com/reel/new_reel/" in jobs[0].payload["body"]


async def test_specific_posts_clear_the_wait(session, account):
    automation = await make_automation(
        session,
        account,
        trigger=comment_trigger(media_ids=["m9"], next_post_word="clone"),
    )
    assert automation.trigger["next_post_word"] == ""
    assert automation.trigger["waiting_since"] is None


async def test_failed_dm_sends_one_alert_per_hour(ctx, session, account, fake_ig):
    await make_automation(session, account)
    # A non-retryable error on two different comments
    fake_ig.send_errors = [api_error(100, message="Invalid parameter") for _ in range(2)]

    await handlers.handle_event(ctx, comment("link", comment_id="c1", user="u1"))
    await handlers.handle_event(ctx, comment("link", comment_id="c2", user="u2"))
    await session.commit()

    jobs = await alert_jobs(session)
    assert len(jobs) == 1
    assert "failed" in jobs[0].payload["subject"]


async def test_email_alert_job(session, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    async def fake_send(config, subject, body):
        sent.append((config.to, subject, body))

    monkeypatch.setattr(alerts, "send_email", fake_send)

    # Not set up yet: the job finishes without sending
    await alerts.process_email_alert({"subject": "s", "body": "b"})
    assert sent == []

    await save_values(
        session,
        {"smtp_username": "me@gmail.com", "smtp_password": "abcdefghijklmnop"},
    )
    await session.commit()
    await alerts.process_email_alert({"subject": "s", "body": "b"})
    assert sent == [("me@gmail.com", "s", "b")]


async def test_waiting_since_survives_a_resave(session, account):
    automation = await waiting_automation(session, account)
    first = automation.trigger["waiting_since"]
    assert first

    again = CommentTrigger.model_validate(automation.trigger)
    assert again.waiting_since == datetime.fromisoformat(first)
    stored = await session.get(Automation, automation.id)
    assert stored.trigger["waiting_since"] == first


async def test_worker_links_waiting_automation_with_laptop_off(session, account, fake_ig):
    from app.jobs import worker

    automation = await waiting_automation(session, account)
    fake_ig.media = [post("new_reel", "Comment CLONE")]

    await worker.check_new_posts()

    await session.refresh(automation)
    assert automation.trigger["media_ids"] == ["new_reel"]


async def test_worker_survives_instagram_errors(session, account, fake_ig, monkeypatch):
    from app.jobs import worker

    automation = await waiting_automation(session, account)

    async def broken(limit=24, after=None):
        raise api_error(2, status=500, message="Service temporarily unavailable")

    monkeypatch.setattr(fake_ig, "list_media", broken)
    await worker.check_new_posts()  # logged, not raised

    await session.refresh(automation)
    assert automation.trigger["next_post_word"] == "clone"
