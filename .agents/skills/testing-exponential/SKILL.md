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
