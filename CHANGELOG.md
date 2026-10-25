# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once tagged releases begin.

## [Unreleased]

### Added

- Open-source project scaffolding (README, LICENSE, CONTRIBUTING, SECURITY, CI)
- Configurable `APP_NAME` / `DEFAULT_MAIL_DOMAIN` branding for fresh installs
- **i18n:** UI + docs in English, Vietnamese, Chinese (Simplified), and Russian
  - Runtime: `static/i18n.js` + `static/locales/{en,vi,zh,ru}.js`
  - Language switcher on login and in the header
  - Localized READMEs and deploy/API overviews under `docs/{en,vi,zh,ru}/`

### Changed

- Default brand string is `OTP Inbox` (override with `APP_NAME`)
- Cloudflare Worker lives only under `worker/` (removed duplicate root copies)
- **Project restructure:** FastAPI application moved into `app/` package; entrypoint is now `uvicorn app.main:app`; demo seed is now `python -m app.seed`; deployment config moved to `deploy/koyeb.yaml`
- **Docs:** added `docs/SETUP.md` as primary getting-started guide (translated to vi/zh/ru); `docs/README.md` index updated to feature SETUP prominently
