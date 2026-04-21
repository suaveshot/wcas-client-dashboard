# CI workflow templates

These YAML files are production-ready GitHub Actions workflows for this repo, but they are stored here (not in `.github/workflows/`) because pushing them requires the `workflow` OAuth scope on the gh CLI token.

## To enable

Run on your local machine:

```bash
gh auth refresh -s workflow -h github.com
# Complete browser auth
mkdir -p .github/workflows
cp docs/ci-templates/security.yml.template .github/workflows/security.yml
git add .github/workflows/security.yml
git commit -m "enable security CI workflow (secrets scan, pip-audit, em-dash check)"
git push
```

## What's here

- **security.yml.template** - runs gitleaks (secrets scan), pip-audit (dependency CVEs), and an em-dash check on every push to main, every PR, and on a weekly schedule Monday morning.
- **uptime.yml.template** - pings the live dashboard /healthz every 10 minutes from GitHub's infrastructure; fails the job on non-200 or missing `"status":"ok"`, which sends the repo owner an email. Fully external uptime monitor, zero cost, no extra account needed.

When these are enabled, Dependabot alerts + gitleaks + pip-audit + the em-dash brand rule + external uptime monitoring all run automatically on every commit and every 10 minutes.
