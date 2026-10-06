---
name: testing-exponential
description: How to run and test the Exponential member portal locally (env vars, dev OTP auth, CSRF, invite/email flows on SQLite)
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

## CSRF (cookie-based double-submit, cookie name `exp_csrf`)
- The middleware mints the token before rendering (`request.state.csrf`) and sets a 1-year cookie, so the first submit in a fresh browser should work. If a first submit in a fresh/incognito window ever 403s again, that is a regression; report it rather than working around it.
- To simulate a stale token in the browser console: `document.cookie='exp_csrf=stale; path=/'` (the cookie is not httpOnly), or set a form's hidden `input[name=csrf]` value. `/apply` should then re-render with a 422, the message "Your session timed out…", and the answers preserved. Other forms in a browser should show a 403 HTML "This page has expired." page with a same-origin return link. Non-HTML clients (curl without `Accept: text/html`) still get JSON `{"detail":"Bad CSRF token"}`.
- Read status codes with `performance.getEntriesByType('navigation')[0].responseStatus`.
- `browser_console` sometimes returns `undefined` for multi-statement snippets. Use a single expression instead.

## Invite a fresh member (admin UI)
- /admin/invitations → "Send invitation" → the link is in the invitation email's body_text: query `dev.db` `email_messages` table (`kind='invitation'`) — the console adapter's log line may not appear in uvicorn output. The /admin/email outbox lists emails but not bodies.
- Open /invite/{token} signed out → "Continue with {email}" → dev sign-in → back on invite page → "Accept invitation as {email}" → /onboarding.

## Manual member creation / "Make member" (admin)
- /admin/members has a collapsed `<details>` "Add a member" card → POST /admin/members/new; application list rows and detail pages have "Make member" (JS `confirm()`; the harness shows the native dialog and Cancel/OK can be clicked).
- The console email adapter marks emails `sent` immediately. Welcome email: kind `member_welcome`, subject "You're a member of Exponential". Read bodies from `email_messages.body_text` (no sqlite3 CLI; use `.venv/bin/python -c "import sqlite3; ..."`).
- To test score copy, set `applications.score/score_source/score_reason` in the DB before converting. To test cap, set the cap in /admin/settings to the "N of M seats" value (settings refuses values below seats in use).
- A member application hides the "Send membership invitation" card. Redeeming an application-linked invite marks the application "member", so its row no longer shows "Make member".
- After a manual add, the member signs in with dev OTP and lands on /onboarding with name, intro and LinkedIn prefilled. "Skip for now" on the photo step → publish → /welcome; the directory shows a monogram.
- Narrow check: `wmctrl -r :ACTIVE: -b remove,maximized_vert,maximized_horz && wmctrl -r :ACTIVE: -e 0,0,0,1000,1480` (real px on the 3200x2400 display, scale 2 → ~530 CSS px). Re-maximize afterwards.

## Testing required-field gates behind HTML `required`
- Forms enforce `required` client-side too, so the browser blocks empty submission. To test SERVER-side rejection, submit with `document.querySelector('form[action="..."]').submit()` in the console — form.submit() skips constraint validation.

## Visual / responsive checks (design PRs)
- Chrome on Linux refuses window widths below ~530px (`xdotool windowsize 390 ...` snaps to 532), so for a ~390px mobile viewport open DevTools (F12) THEN press Ctrl+Shift+M for device emulation and type the width in the "Dimensions" box. (Ctrl+Shift+M without DevTools open pops the Chromium profile menu.) Toggle back with Ctrl+Shift+M + F12 before desktop checks.
- Assert overflow with `document.documentElement.scrollWidth === innerWidth`; if content overflows, emulation may report a widened `innerWidth` (e.g. 438 for a 390 device) — that itself is the failure signal. Find the culprit with `[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1)`.
- Transparent-logo checks: PNGs with alpha can still render white halos; sample pixels of a root screenshot (`import -window root x.png`, PIL) inside the `<img>` bounding box and compare against `getComputedStyle(body).backgroundColor` (paper is rgb(251,250,248)).
- Watch for `<br class="desk">`-style responsive line breaks hidden at ≤720px: if the `<br>` is the only whitespace between words, hiding it fuses them ("acceleratingtechnological") and can overflow small viewports. Prefer a space plus `<br>` or `<span class="desk-break">`.
- The omnibox autocompletes `localhost:8899/` to previously visited deeper paths; press Delete after typing the URL (before Enter) to drop the suggestion.
- Seeded members (e.g. mara.chen) have no LinkedIn; since LinkedIn is required on /members/profile, add one before expecting "Profile saved.".

## Browser harness gotchas
- The Devin browser harness may strip `target` attributes from the live DOM (it adds `devinid` attrs), so `target="_blank"` links open in the SAME tab in the recorded Chrome. Prove the server sends the attribute with `fetch(location.href)` + DOMParser in the console, and prove new-tab behavior in a clean headless Playwright Chrome (`executable_path=/opt/.devin/chrome/chrome/linux-*/chrome-linux64/chrome`, dev sign-in via the on-page code, `ctx.expect_page()`).
- If the CDP Chrome on :29229 disappears, relaunch it yourself: `DISPLAY=:0 setsid nohup <chrome> --remote-debugging-port=29229 --user-data-dir=$HOME/.config/google-chrome-for-testing --force-device-scale-factor=2 --start-maximized about:blank &`. Without the scale factor the 3200x2400 display renders unreadably small. Launch it in a separate exec from any `pkill -f`: a pkill pattern that matches its own command line kills the shell (exit -1).
- xdotool `type` can drop `?` in URLs (`/admin/members?status=paused` became `/admin/membersstatus=paused` → 404). Use the page's own filter controls instead of typing query strings.
- For "empty state spans full width" checks, compare `td.colSpan` and `td.getBoundingClientRect().width` against `thead tr` width.

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
