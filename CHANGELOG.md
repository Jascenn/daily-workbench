# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-26

Initial public release.

### Added

- **Core**: single-file frontend (`workbench.html`, no build, no framework) with a
  stdlib-only Python backend (`app.py`). Token-authenticated local API on
  `127.0.0.1:8787` with atomic writes, 20-slot rotating backups, and a
  sudden-drop guard against accidental data wipes.
- **Earnings**: Notion read-only sync, local draft ledger with explicit
  push-to-Notion confirmation, and a `sourceMap` merge engine that groups
  different note spellings of the same client. Conservation invariants
  (count & amount) are test-guarded.
- **Insights**: "top three things today" deck, monthly goal gap analysis,
  earnings trend charts, source ranking, expense breakdown, data health
  checks (duplicates / zero-amount / gap / project-link rate) with
  jump-to-record navigation.
- **Social snapshots** (all read-only, honest "not connected" states instead
  of fake zeros): X (via xreach session), WeChat official account (mpdt
  pipeline + completion-rate leaderboard), Xiaohongshu, and Zsxq (live
  aggregation + historical export fallback).
- **Web presence**: uptime checks (5-min interval), blog RSS tracking,
  Google Search Console daily traffic, Umami visitor stats.
- **Content screens**: news reader with tag/source filters and feed
  management (probe / add / delete RSS), writing tracker, review notes with
  Obsidian debug-experience integration, publish checklist with
  copy-as-tweet, weekly report generator, prediction backfill, ops health
  score.
- **AI plugin (optional)**: TypeSafe Jev enhancement toggle — off means the
  page behaves exactly as before; on adds content-quality analysis with
  data-aware insights.
- **UX**: command palette (`Cmd/Ctrl+K`), keyboard shortcuts (guarded
  against firing while typing in form fields), guest mode with fully
  isolated sample data, undo-instead-of-confirm deletes, dark mode with
  tokenized design system, offline fallback page, in-page notification bar.
- **Sync**: one-command `sync-now.sh` with startup-style step logging
  (last 20 runs kept, latest run expandable), auto-sync on page open when
  data is stale (>1 day), 409 on concurrent syncs.
- **Tests**: 499 logic assertions (expectations derived from real data,
  never hardcoded), 147 UX smoke checks, 69 mutation tests that must all
  turn red, plus a live end-to-end suite.

### Security

- All personal identifiers (Notion database IDs, social handles, local
  paths) are parameterized via environment variables; the repository ships
  with neutral defaults and zero personal data.

[Unreleased]: https://github.com/Jascenn/daily-workbench/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Jascenn/daily-workbench/releases/tag/v1.0.0
