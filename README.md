# FlowDM: self-hosted Instagram DM automation

A self-hosted replacement for ManyChat's Instagram automations. It runs on one small server you
control (an AWS free-tier EC2 instance works), with no monthly subscription and no contact
limits.

## What it does

* **Comment → DM**: someone comments a keyword (e.g. `LINK`) on a post or reel and instantly
  gets a DM. Optionally the comment also gets a public reply, picked at random from your
  variants ("Sent you a DM! 📩"). You can match keywords by *contains*, *exact*, or *any
  comment*, limit it to specific posts, and DM each person only once.
* **My next post**: set up the automation before a scheduled reel goes live. Instagram only
  gives a post its id once it is live, so the automation waits with a caption word (e.g.
  `CLONE` for a caption that says "Comment CLONE"). The server checks your newest posts every
  2 minutes, and on the first comment, then links the automation to the matching post and
  starts replying. Nothing has to stay open on your computer, and older posts are never touched.
* **Email alerts**: an email when a DM fails, when a new post goes live while an automation is
  waiting but its caption lacks the word, or when Instagram access can't be renewed. Set the
  sending Gmail address and an app password under Settings; bursts are grouped into one email.
* **Multi-step flows** built in a form-based editor:
  * messages with up to 3 buttons (go to another step, or open a link)
  * image cards
  * **follow-gate** ("follow me first, then tap to get the link"), using a real follow check
  * **collect email / phone / any answer**, with validation and retries; saved on the contact
  * delays between steps
  * tags for segmenting leads
  * personalization: `{first_name}`, `{username}`, `{email}`
* **DM keyword triggers**: reply when someone DMs a keyword.
* **Contacts**: everyone who interacted, with email/phone/tags, full conversation history, and
  CSV export.
* **Activity log**: every comment, the decision made, every flow run, raw webhooks and errors,
  so you can see exactly why something did or didn't fire.
* **Reliable by design**: webhooks are stored before processing, duplicates from Meta are
  ignored, rate limits and Instagram outages are retried with backoff, and access tokens refresh
  automatically before their 60-day expiry.

## How it works

```
Instagram ──webhook──▶ Caddy (HTTPS) ──▶ FastAPI ──stores event + job──▶ PostgreSQL
                          │                                                  │
   Browser ◀── React dashboard                       Worker ◀── claims jobs ─┘
                                                        │
                         Instagram Graph API ◀── private reply / DMs / public replies
```

| Part | Tech |
|---|---|
| API + webhooks | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic |
| Background worker | Same codebase; durable job queue in Postgres (`FOR UPDATE SKIP LOCKED`), no Redis needed |
| Dashboard | React 19, TypeScript, Vite, Tailwind CSS |
| Database | PostgreSQL 16 |
| HTTPS + static files | Caddy (free automatic Let's Encrypt certificates) |
| Deployment | Docker Compose; about 250 MB RAM total |

Instagram is accessed through the official **Instagram API with Instagram Login** using your
own Meta app.

## Getting started

1. **Deploy**: [docs/DEPLOY_AWS.md](docs/DEPLOY_AWS.md). Launch one EC2 instance and paste
   [`deploy/aws-user-data.sh`](deploy/aws-user-data.sh) as its *User data*; it installs itself
   (free DuckDNS domain + HTTPS), with no terminal needed.
2. **Connect Instagram**: [docs/META_SETUP.md](docs/META_SETUP.md) (Meta app, webhooks, testers,
   going live).
3. **Run locally / develop**: [docs/LOCAL_DEV.md](docs/LOCAL_DEV.md).

Quick local try-out (needs Docker):

```bash
./deploy/init-env.sh          # creates .env with random secrets, prints your login
docker compose up -d --build  # then open http://localhost
```

## Important: Meta App Review

While your Meta app is in *Development* mode, automations only fire for Instagram accounts
you've added as testers. To respond to **everyone**, the app must be switched to *Live*. Meta's
docs say comment webhooks need **Advanced Access**, which means **Business Verification + App
Review** (free, usually a few days). This is the one step ManyChat has done for you.
[META_SETUP.md §8](docs/META_SETUP.md#8-going-live-so-anyone-not-just-testers-triggers-your-automations)
walks through it. Everything else works with tester accounts in the meantime.

## Project layout

```
backend/
  app/
    api/            REST endpoints (auth, accounts/OAuth, automations, contacts, activity, webhooks)
    engine/         flow runner, event handlers, keyword matching
    instagram/      Graph API client, webhook payload parsing
    jobs/           Postgres job queue + worker (python -m app.jobs.worker)
    models.py       database tables
    schemas.py      automation/flow definitions and validation
  alembic/          database migrations
  tests/            pytest suite (fake Instagram client, real Postgres)
frontend/           React dashboard
deploy/             Caddyfile, web Dockerfile, EC2 setup, env init, backup and update scripts
docs/               setup guides and the HTTP API reference (docs/API.md)
docker-compose.yml
```

## Limits to know

These are Instagram platform rules, not FlowDM limitations:

* After the first DM to a commenter (a "private reply"), nothing more can be sent until they tap
  a button or reply, so start comment flows with a button. The templates already do this.
* Messages can only be sent within 24 hours of the person's last message or button tap.
* About 750 comment-triggered DMs per hour per account. Extra ones are queued, not lost.
