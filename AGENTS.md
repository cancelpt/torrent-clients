# Project Instructions

## Public vs Local Agent Instructions
- `AGENTS.md` is public and must remain commit-safe.
- Use `AGENTS.local.md` for machine-specific overrides. This file is developer-local and must not be committed.
- Instruction precedence for local development:
  1. `AGENTS.local.md` (if present)
  2. `AGENTS.md`
- Real credentials or environment-specific downloader endpoints must never appear in `AGENTS.md`.

## Downloader Integration Data Policy
- Never commit real downloader integration data in this repository.
- The following are prohibited in tests, docs, examples, fixtures, and config samples:
  - real downloader URL / host / port
  - real username / password / token / cookie
  - any environment-specific connection details from integration environments

## Allowed Placeholder Values
- Use placeholders only for downloader connectivity examples:
  - URL: `http://localhost:8080/`, `http://localhost:9091/`, `http://example.com`
  - username: `test_user` (or empty string)
  - password: `test_password` (or empty string)
  - docs placeholders: `<your-transmission-username>`, `<your-transmission-password>`

## Local Customization
- Start from `AGENTS.local.md.example` and create your own `AGENTS.local.md`.
- Keep local overrides focused on developer-specific values only.
- Never stage or commit `AGENTS.local.md`.

## Commit-Time Check
- Before each commit, review staged diffs (`git diff --cached`) and ensure no integration downloader data appears.
- If any real integration value is found, replace it with placeholders before committing.
