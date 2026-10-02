---
name: testing-exponential
description: How to run and test the Exponential member portal locally (env vars, dev OTP auth, CSRF quirks, invite/email flows on SQLite)
---

# Testing the Exponential member portal locally

## Run the app
- Venv at `.venv` (blueprint: `python -m venv .venv`, `pip install -e ".[dev]"`).
- Start: `.venv/bin/uvicorn app.main:app --port 8899` with env vars:
  - `ADMIN_EMAILS=admin@example.com` — admin_emails defaults to empty; without it no one can reach /admin.
  - `APP_BASE_URL=http://localhost:8899` — invitation links in emails are built from this; default is :8000, so set it to match your port or rewrite the port when opening links.
  - Defaults already fine: `AUTH_PROVIDER=dev`, `EMAIL_PROVIDER=console`, `STORAGE_BACKEND=local`, SQLite `dev.db` at repo root. Alembic migrates on boot; `SEED_DEMO_DATA=true` seeds ~10 fictional members.

## Dev auth (AUTH_PROVIDER=dev)
- /auth/sign-in → enter email → 6-digit code is printed ON the verify page ("DEV MODE ... your code is NNNNNN") and in the server log via the console email adapter.

## CSRF quirks (cookie-based double-submit, cookie name `exp_csrf`)
- The middleware sets `exp_csrf` only when the request lacks it, so the FIRST form render in a fresh browser carries an empty csrf field and the first POST fails with `{"detail":"Bad CSRF token"}`. Reload the page and resubmit.
- KNOWN BUG (pre-existing, not your change): the "New invitation" form in `app/templates/admin/invitations.html` lacks `<input type="hidden" name="csrf">`, so POST /admin/invitations always 403s. Workaround for testing: add the hidden input, or create the invitation directly (insert into `invitations` + `email_messages`, or resend an existing one).

## Invite a fresh member (admin UI)
- /admin/invitations → "Send invitation" → the link is in the invitation email's body_text: query `dev.db` `email_messages` table (`kind='invitation'`) — the console adapter's log line may not appear in uvicorn output. The /admin/email outbox lists emails but not bodies.
- Open /invite/{token} signed out → "Continue with {email}" → dev sign-in → back on invite page → "Accept invitation as {email}" → /onboarding.

## Testing required-field gates behind HTML `required`
- Forms enforce `required` client-side too, so the browser blocks empty submission. To test SERVER-side rejection, submit with `document.querySelector('form[action="..."]').submit()` in the console — form.submit() skips constraint validation.

## Visual / responsive checks (design PRs)
- Chrome on Linux refuses window widths below ~530px (`xdotool windowsize 390 ...` snaps to 532), so for a ~390px mobile viewport open DevTools (F12) THEN press Ctrl+Shift+M for device emulation and type the width in the "Dimensions" box. (Ctrl+Shift+M without DevTools open pops the Chromium profile menu.) Toggle back with Ctrl+Shift+M + F12 before desktop checks.
- Assert overflow with `document.documentElement.scrollWidth === innerWidth`; if content overflows, emulation may report a widened `innerWidth` (e.g. 438 for a 390 device) — that itself is the failure signal. Find the culprit with `[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1)`.
- Transparent-logo checks: PNGs with alpha can still render white halos; sample pixels of a root screenshot (`import -window root x.png`, PIL) inside the `<img>` bounding box and compare against `getComputedStyle(body).backgroundColor` (paper is rgb(251,250,248)).
- Watch for `<br class="desk">`-style responsive line breaks hidden at ≤720px: if the `<br>` is the only whitespace between words, hiding it fuses them ("acceleratingtechnological") and can overflow small viewports. Prefer a space plus `<br>` or `<span class="desk-break">`.
- The omnibox autocompletes `localhost:8899/` to previously visited deeper paths; press Delete after typing the URL (before Enter) to drop the suggestion.
- Seeded members (e.g. mara.chen) have no LinkedIn; since LinkedIn is required on /members/profile, add one before expecting "Profile saved.".

## Misc
- Onboarding profile fields render `value="None"` when DB columns are NULL (Jinja None leak) — clear inputs before typing.
- Media: `media/headshots/*.jpg` served via authenticated `/media/` route; monogram fallback color = MONOGRAM_PALETTE[sha256(name) % 6] in `app/images.py`.

## Devin Secrets Needed
- None for local dev testing (all adapters are dev/console by default).

## Production (Clerk) sign-in testing — https://exponential.nyc
- Prod runs AUTH_PROVIDER=clerk with a pk_live/sk_live instance (Frontend API `clerk.exponential.nyc`). Secret: `CLERK_SECRET_KEY` (session secret). Never print it; check it works with `GET https://api.clerk.com/v1/instance` (should say `environment_type: production`).
- **Org secret `CLERK_JWKS_URL` points at a *different* app's dev instance (SituationMonitor, `*.clerk.accounts.dev`), and it's injected into every shell.** When running `app.clerk` locally, always override `CLERK_JWKS_URL=https://clerk.exponential.nyc/.well-known/jwks.json`, or you'll get a misleading "Unable to find a signing key that matches" error.
- Throwaway user: `POST /v1/users {"email_address":["devin-test+<ts>@exponential.nyc"],"skip_password_requirement":true,"skip_password_checks":true}` returns the email already `verified`. Ticket: `POST /v1/sign_in_tokens {"user_id":..,"expires_in_seconds":900}` (the ticket is single-use). Cleanup: `DELETE /v1/users/{id}`, then `GET` should return 404.
- `POST /v1/sessions` is dev-instance only (`request_invalid_for_environment`), so you can't mint session JWTs server-side in prod.
- Sign in in the browser by opening `/auth/sign-in?__clerk_ticket=<ticket>`. The mounted SignIn widget consumes it, reloads, and the page script POSTs `{token,next}` to `/auth/clerk`. To keep the ticket and JWT out of logs, drive this with Playwright `connect_over_cdp("http://localhost:29229")`. Read the ticket from a 0600 file, and capture the `/auth/clerk` request/response with `page.on('request'/'response')`. Session JWTs expire in about 60s, so verify a captured token locally right away.
- If the exchange fails, the page stores the error in sessionStorage, signs out of Clerk and reloads `/auth/sign-in`, where it shows the error above the widget. Check the `/auth/clerk` response itself too.
- If the exchange fails before `_upsert_user` (any 401), no app `users` row is created.
- Expected after a successful exchange for a non-member: redirect to `next` (default `/`), nav shows only "Sign out"; `/members` and `/directory` → 303 `/` (`require_member`, no seat); `/admin` → 404.
- In Clerk mode, POST `/auth/sign-out` clears the app session cookie and renders a "Signing you out…" page. That page loads Clerk.js and calls `Clerk.signOut({redirectUrl: '/'})`, falling back to `/` on error or after 5 seconds. Dev auth continues to redirect directly to `/`.
- To end leftover Clerk sessions between browser runs, use the Backend API: `GET /v1/sessions?user_id=..`, then `POST /v1/sessions/{id}/revoke`. Deleting the test user also ends their sessions.
- After `/auth/clerk` succeeds, the page navigates away immediately. Playwright's `response.json()` may fail ("No resource with given identifier"), so read `response.status` first and treat the body as optional.
- HTML responses, including `/`, use `Cache-Control: private, no-cache` so the user-dependent nav is revalidated; static assets retain their existing caching behavior.
- `www.exponential.nyc` serves the app directly (no redirect to the apex) and shares the Clerk Frontend API. A ticket sign-in works there too because the azp allowlist includes www.
- Do not touch Railway vars, the prod DB, or real accounts; do not send invitations or emails.

### Verifying Clerk sign-out (prod)
- In Clerk mode `POST /auth/sign-out` renders `auth/signout_clerk.html` ("Signing you out…"), which calls `Clerk.signOut({redirectUrl:'/'})`. The page only lasts about 0.4s, so a normal screenshot misses it. Attach a Playwright CDP listener (framenavigated + console + request/response) *before* clicking Sign out in the UI, and call `page.screenshot()` when the main frame navigates to `/auth/sign-out`.
- Proof that Clerk was signed out: the listener shows `POST clerk.<domain>/v1/client/sessions` → 200, and `GET https://api.clerk.com/v1/sessions?user_id=<id>` reports the status as `removed` (it was `active` before the fix).
- To check that sign-in doesn't happen silently, click "Sign in" and wait at least 6s. The URL should stay `/auth/sign-in` with the "Email address" field showing.
- Redirect checks after typing a URL: run `performance.getEntriesByType('navigation')[0].redirectCount` in the console. A value of 1 plus a new `timeOrigin` proves a redirect happened even when the screenshot looks the same.
- Check `Cache-Control` with GET; `HEAD /` returns 405.
