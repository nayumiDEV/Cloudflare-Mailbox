# Security Policy

## Supported versions

Security fixes are applied to the latest `main` branch. There are no long-term
support branches yet — please upgrade to the newest commit when a fix lands.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Report privately instead:

1. Use [GitHub Security Advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) on this repository (“Report a vulnerability”), **or**
2. Email the maintainer listed in the repository profile / README.

Include:

- A clear description of the issue and its impact
- Steps to reproduce (PoC if possible)
- Affected version / commit hash
- Whether you are aware of active exploitation

You should receive an acknowledgement within a few days. We will coordinate a
fix and a public disclosure timeline with you.

## Hardening checklist for operators

- Set a long random `ADMIN_PASSWORD` and `WORKER_INGEST_TOKEN` before first boot
- Keep `COOKIE_SECURE=1` behind HTTPS
- Never commit `.env`, database files, or Wrangler secrets
- Rotate the admin API key if it was ever printed in logs
- Prefer PostgreSQL (or a volume-backed single SQLite instance) over ephemeral disk
- Leave registration and referral-code generation **off** until you intentionally open them
