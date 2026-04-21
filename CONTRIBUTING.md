# Contributing

This is a hackathon-submission repo, so contributions during the hackathon week (Apr 21-26, 2026) are limited. After Apr 27, happy to accept issues and PRs.

## Data handling rules (non-negotiable)

1. **Never commit any file under `/opt/wc-solns/<tenant>/`.** That's where real client state lives.
2. **Never commit `tenant_config.json`, `goals.json`, `baseline.json`, `brand.json`, `kb/*.md`, or `dashboard_decisions.jsonl`.** The `.gitignore` blocks them, but also don't manually work around it.
3. **Never commit `.env*` files** except `.env.example` with placeholder values only.
4. **Never commit OAuth token JSONs, credential files, refresh tokens, API keys, PATs, or service account JSONs.**
5. **Run the pre-commit secret scanner** (`.githooks/pre-commit`) on every commit. It's auto-installed on `git config core.hooksPath .githooks`.

## Code style

- Python: ruff + standard library first; no frameworks beyond FastAPI + Anthropic SDK + pyairtable.
- HTML/CSS/JS: static, vanilla, no build step. Uses the WCAS brand tokens from `static/styles.css`.
- **No em dashes anywhere.** Use commas, periods, or spaced hyphens. This is enforced by the guard-rail review pass at runtime AND checked at commit time.

## Commit message style

- Keep under 72 chars in the subject line.
- Lower-case verb first: "add X", "fix Y", "refactor Z".
- No co-author or generated-by tags unless actually co-authoring.

## Questions

Open an issue or contact info@westcoastautomationsolutions.com.
