# Exponential

The website and private member portal for **Exponential Collective** — a selective NYC community for people building, researching, and thoughtfully applying AI (salons, breakfasts, small dinners).

Server-rendered FastAPI app. No JS build step. Python 3.11+.

## Quick start (local dev)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

The app auto-migrates the database on boot (`alembic upgrade head`). Dev defaults are fully self-contained:

- `AUTH_PROVIDER=dev` — passwordless email sign-in; the 6-digit code is printed **on the sign-in page itself** when `EMAIL_PROVIDER=console` (no email service needed).
- `EMAIL_PROVIDER=console` + `EMAIL_DISABLE_SENDS=true` — all email lands in the outbox (`/admin/email`) and server logs; nothing is delivered.
- `STORAGE_BACKEND=local` — media under `./media`, served only through authenticated `/media/…` routes.
- `DATABASE_URL=sqlite:///exponential.db` — SQLite in the repo root.

Fictional demo data (10 members + events, `@example.com` emails only): set `SEED_DEMO_DATA=true`. It refuses to run in production.

### Becoming an admin in dev

Set `ADMIN_EMAILS=you@example.com` in `.env`, then sign in with that address — the first sign-in matching the list becomes admin. Admin is **only** ever granted this way; there is no in-app admin promotion.

### Tests

```bash
pytest          # 20 workflow tests: invites, cap race, authz, media, CSRF, etc.
ruff check .
```

## Production configuration

| Area | What you need |
|---|---|
| Auth | `AUTH_PROVIDER=clerk`, `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` ([clerk.com](https://clerk.com) → create app → API Keys). Enable email verification in the Clerk app. |
| Database | `DATABASE_URL=postgresql+psycopg://…` (Neon/Supabase/RDS — anything Postgres). |
| Email | `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, `EMAIL_FROM` on a verified domain. Flip `EMAIL_DISABLE_SENDS=false` only when ready to send. |
| Media | `STORAGE_BACKEND=s3` + `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`. Bucket must be **private**; the app serves files through `/media/…` with auth checks. |
| Admins | `ADMIN_EMAILS` — you + cohosts. |
| Misc | `SECRET_KEY` (openssl rand -hex 32), `APP_BASE_URL`, `ENVIRONMENT=production`, `SESSION_COOKIE_SECURE=true`. |
| Signal | Set the invite link in **Admin → Settings** (no env var needed). |

## Deploy (Fly.io)

```bash
fly launch          # creates fly.toml; mount a volume at /data for sqlite+media
fly secrets set SECRET_KEY=... ADMIN_EMAILS=... AUTH_PROVIDER=clerk ...
fly volumes create expdata --region iad --size 1
# fly.toml mounts: [[mounts]] source="expdata" destination="/data"
# then DATABASE_URL=sqlite:////data/exponential.db  MEDIA_DIR=/data/media
fly deploy
```

## Architecture

- `app/main.py` — app factory, security headers/CSP, robots, rate-limited surface.
- `app/routers/` — `public` (home, apply, invite claim), `auth`, `onboarding`, `member` (welcome, directory, profile, events), `admin`, `media`.
- `app/models.py` — users, members, profiles, applications, invitations, events, email outbox, private notes, audit log, settings, removal requests.
- `app/services/admissions.py` — seat accounting (`invited`+`active`+pending invites ≤ cap), status transitions, settings.
- `app/security.py` — signed session cookies (bumped `session_version` = instant revocation), role deps, CSRF, rate limits.
- `app/emailer.py` — outbox + provider adapters (`console`, `resend`). Sends happen via `EMAIL_DISABLE_SENDS` gate.
- `app/storage.py` / `app/images.py` — local/S3 backends, Pillow resize/crop, EXIF strip.
- `alembic/` — migrations (`alembic upgrade head` also runs on boot).

### Design decisions worth knowing

- **Seat model:** `invited` + `active` members plus pending invitations count against `MEMBER_CAP`. An issued invite already holds a seat, so redemption never re-checks capacity — a redeemed invite simply converts its reserved seat. Approvals/invites serialize on a DB lock (`SELECT FOR UPDATE` on Postgres, write-lock on SQLite).
- **Invite links** are 32-byte single-use tokens; only the SHA-256 hash is stored. Resend **rotates** the token (old links die). 14-day expiry.
- **Clerk vs member:** Clerk identity is just login. Membership lives in `members.status`. Admins map users→member rows; admin status is env-bootstrapped only.
- **Media is never public.** Headshots re-encode to 800px JPEG with EXIF stripped and are served via `/media/…` to seat-holders/admins only (`private, no-store`, `X-Robots-Tag: noindex`).
- **Private admissions notes** never appear in member-facing pages or CSV exports.
- **Dev adapters are labeled** in the UI (console email shows codes/links inline; dev sign-in shows the OTP on screen) so missing credentials never block development.

## Content notes

Public pages intentionally contain no member names, photos, events, addresses, or testimonials. Directory and member area require an `active` (or `invited`, for onboarding) seat. All copy is original — update `app/templates/public/` when the community's voice settles.
