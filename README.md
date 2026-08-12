# agentjob

Local-first AI job-search agent with a lightweight web dashboard.

`agentjob` connects to a user-controlled Chrome/Edge session through CDP (`127.0.0.1:9227`), collects job details, applies deterministic hard filters, supports AI fit review and company/job due diligence, and keeps the final apply action behind explicit human confirmation.

## What is included

- L0: job discovery + full-detail capture
- L1: deterministic hard filters
- L2: AI fit review (API or external-agent bridge)
- L3: company / job due diligence with source matrix
- L4: communication + resume focus materials
- L5: explicit user confirmation + one initial apply/contact action
- L6: unsupervised recruiter chat is disabled by default
- Dashboard on `http://127.0.0.1:8799`
- Light/dark themes, favorites, skip/hold states, resume source storage
- Verified-send protection: once an application is verified for the current JD version, it cannot be sent again unless the JD meaningfully changes

## Safety / account model

This project does **not** include CAPTCHA bypass, stealth/fingerprint spoofing, or anti-bot evasion. If BOSS shows a security verification page, the worker stops and waits for the user to handle it manually.

The browser profile, cookies, resume PDF, database, profile files and logs are local-only and ignored by Git.

## Quick start (Windows)

1. Install Python 3.11+ and Google Chrome or Microsoft Edge.
2. Run `install.bat`.
3. Edit the generated local files from the Dashboard, or directly if needed:
   - `我的资料.txt`
   - `求职要求.txt`
   - `补充资料.txt`
4. Run `run_agent.bat`.
5. Log in to BOSS in the dedicated browser opened on port 9227.
6. Open `http://127.0.0.1:8799`.

The first run copies files from `examples/` into local ignored runtime files.

## AI configuration

Optional OpenAI-compatible model configuration is supplied via environment variables:

- `BOSS_AI_ENDPOINT`
- `BOSS_AI_API_KEY`
- `BOSS_AI_MODEL`

If they are absent, L2 stays in `needs_ai` and an external agent can review the request JSON produced by `agent_bridge.py`.

## Privacy

Never commit:

- `browser-profile/`
- `data/`
- resume PDFs
- local profile/preference TXT files
- API keys/tokens

The repository ships example files only.

## Status

MVP. The current focus is a reliable local workflow and explicit state transitions rather than fully autonomous recruiter communication.
