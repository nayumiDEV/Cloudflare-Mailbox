# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once tagged releases begin.

## [Unreleased]

### Added

- Open-source project scaffolding (README, LICENSE, CONTRIBUTING, SECURITY, CI)
- Configurable `APP_NAME` / `DEFAULT_MAIL_DOMAIN` branding for fresh installs

### Changed

- Default brand string is `OTP Inbox` (override with `APP_NAME`)
- Cloudflare Worker lives only under `worker/` (removed duplicate root copies)
