# Security Checklist for Public Deployment

This checklist is for this repository's current scope (Streamlit civic lookup app with external API calls).

## 1) Secrets and API Keys
- Keep `.streamlit/secrets.toml` out of source control (already in `.gitignore`).
- Use distinct keys per environment (dev/stage/prod).
- Rotate keys immediately if leaked in logs, screenshots, or issue threads.
- Do not print secrets in UI, stack traces, or structured logs.
- OpenStates key is sent in `X-Api-Key` header (preferred for URL hygiene).
- Congress.gov currently uses `api_key` query parameter for compatibility.
- If deployed behind a reverse proxy or WAF, disable query-string logging or redact `api_key`.

## 2) Transport and Network
- Enforce HTTPS end-to-end.
- Restrict outbound network to required hosts: `v3.openstates.org`, `api.congress.gov`, and `nominatim.openstreetmap.org`.
- Apply request timeouts for all external calls.
- Apply conservative retry behavior only for transient rate limits.

## 3) Input and Output Safety
- Treat all user-provided location strings as untrusted input.
- Never execute or eval user input.
- Keep output rendering in markdown/text only and avoid unsafe HTML rendering.
- Keep errors actionable but sanitized (no raw exception dumps containing internals).

## 4) Access Control and Auth Scope
- Current repo has no user auth subsystem.
- Login/logout/password-reset controls are N/A until auth is introduced.
- If auth is added later, require secure logout invalidation, expiring reset tokens, CSRF controls, and brute-force rate limits.

## 5) State and Abuse Controls
- Prevent duplicate submissions while a request is in-flight.
- Ensure refresh/re-entry does not crash state handling.
- Add rate limiting upstream (proxy/CDN) to reduce abuse and scraping pressure.

## 6) Mobile and UX Resilience
- Validate key actions on narrow viewports before release.
- Ensure primary action controls remain visible and non-overlapping.

## 7) Dependency and Supply Chain
- Pin and regularly update dependencies.
- Run tests on every PR.
- Add dependency vulnerability scanning in CI (for example, `pip-audit`).

## 8) Logging, Monitoring, and Incident Readiness
- Log request failures and status codes without sensitive payloads.
- Alert on sustained 4xx/5xx/429 spikes.
- Keep a rollback procedure (branch/tag) for rapid restore.

## 9) Verification Gates Before Release
- Run the full automated test suite.
- Confirm OpenStates key appears only in headers.
- Confirm Congress key behavior remains intentionally query-based unless endpoint verification proves header parity.
- Recheck docs and deployment commands against actual entrypoint file names.
