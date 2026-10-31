# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Move the feed source of truth to a workspace-owned `rss-store.json` with atomic writes, timestamped backups, and an explicit `--import-qiaomu` one-time seed; the runtime never touches the Obsidian plugin file.
- Derive item source names from the subscription name so plugin-side name drift can no longer detach feeds from their entries.
- Add selectable feed-health sorting (stale-first, freshest, item count, group) persisted per browser, with a non-squeezing card header.
- Add server-side feed refresh with a proxy fallback for sources the plugin cannot reach.
- Preserve `<details>` folds from fetched articles and render them as native collapsible sections with per-whitelist recursion.
- Add block-level copy buttons and click-to-zoom for reader images.
- Add news search, source/group/unread filters, one-click reset, recent/dead-feed overviews, and clickable source history.
- Keep feed probing and subscription management reachable from the compact mobile news summary.
- Add writing-page next-chapter focus, goal progress, collapsible source materials, and collapsible chapter/client archives.
- Add a writing-first action panel, direct current-chapter resume link, remaining-word target, highlighted next chapter, and a default-collapsed client card.
- Add in-workbench Obsidian Markdown editing with live safe preview, autosave/manual save, SHA-256 conflict detection, backup rotation, and atomic writes.
- Add Obsidian wikilink and local-image preview support with a token-protected, path-bounded image endpoint.
- Add safe Obsidian deep links for writing outlines, written chapters, source documents, drafts, and review notes.
- Add media overview summaries plus default-collapsed account, website, performance, brief, signal, and knowledge sections.
- Add review search and type filters with a recent-record focus, collapsible history, and compact prediction, weekly-report, and note-history tools.
- Add a focused overview with collapsed daily timeline, progress-first monthly goals, and on-demand goal settings plus six-month achievement history.
- Add a focused project page with actionable metrics first and default-collapsed closure, priority, delivery, and hours evidence.
- Add a focused customer page with follow-up risk first, a compact health-check summary, and one-click filter reset.
- Split the pomodoro panel into a focus workspace and a world-time pane with Shanghai as the primary clock and two configurable candidate cities numbered 1-2.
- Add candidate selectors to the world-clock card so the compact time pane stays display-only while clock choices remain editable below.
- Collapse project focus details and the full world-clock workspace behind summaries while keeping the primary timer and candidate clocks visible.
- Separate candidate clocks into distinct cards with larger gaps, clearer hierarchy, and roomier time labels.
- Collapse the empty focus-trend chart into a compact summary while keeping the full chart available after real sessions exist.
- Add per-stage sync timings and highlight the slowest stage in the workbench sync log.
- Add schema-driven project writeback for project fields, tags, dates, hours, parent relations, and notes.
- Add searchable project owner, related-task, and source-record editors backed by read-only Notion summaries and whitelist-only writeback.
- Add project type, sprint, owner, child-count, relation, and related-link presentation on project cards.
- Add explicit `已延迟` project status handling and keyboard navigation for workbench page switching.
- Add a whitelist-only project writeback test suite with local rollup preservation checks.
- Add a pull-request template with privacy and read-only-boundary checks.
- Add baseline repository hygiene files and monthly GitHub Actions update checks.
- Add a security policy, issue forms, and a CODEOWNERS map.

### Changed

- Align the in-app reader title, h2, and h3 sizes, weights, and margins with the lingyi.me article baseline.
- Trap keyboard focus inside open dialogs, focus the first available control on open, restore the trigger on close, and lock background scrolling.
- Add explicit accessible names to the overview note, goal amount, and earnings search/filter controls instead of relying on placeholders.
- Prioritize actionable content on the news, writing, media, and review pages, while keeping complete data available through disclosure controls.
- Increase every primary media disclosure row to a 44px touch target and let performance titles use two lines on mobile instead of truncating the comparison away.
- Make review tool disclosures and note-history controls 44px tall, expand inline Obsidian link hit areas without changing row density, and preserve project names with a two-line mobile clamp.
- Hide the review clear-filter action until a filter is active, matching the news and customer toolbars and reducing default toolbar noise.
- Prioritize today's actions and goal progress on the overview page while keeping notes, timeline, goal settings, and full business evidence available in place.
- Prioritize current and at-risk projects, keep the project board at its content height, and separate completed from cancelled scopes.
- Prioritize customer follow-up gaps on the first screen, keep secondary health checks collapsed, and reduce the empty follow-up state to one action row.
- Reduce the time page's repeated empty-state action, project breakdown, and full city controls into compact summaries with on-demand expansion.
- Reduce the news page height from about 2447px to 1140px on desktop and from 3599px to 1810px on mobile.
- Reduce the writing page height from 1422px to 959px on desktop and from 1905px to 1416px on mobile.
- Reduce the media page height from 2961px to 1213px on desktop and from 5724px to 1593px on mobile.
- Reduce the review page height from 2825px to 1021px on desktop and from 5334px to 2017px on mobile.
- Reuse the latest synchronized Notion project schema instead of fetching it again for every save.
- Remove WorkBuddy branding and obsolete WorkBuddy-specific runtime assumptions from the independent workbench.
- Align project and customer writeback subprocesses with the macOS system proxy and launchd path environment.

### Fixed

- Raise the full sync timeout from 180s to 300s so normal Notion, social, and website-monitoring latency does not produce a false 504.
- Record timed-out syncs in sync history and terminate the entire sync process group so background children cannot keep running.
- Fix duplicate source-card IDs that wrote the source count into the overview card while leaving the data-source dialog header blank.
- Retry transient Notion proxy failures without retrying validation, authentication, or permission errors.
- Fix project-page horizontal overflow at tablet and narrow-desktop widths by stacking the page header and allowing source text to wrap.
- Keep the media page's hero metrics readable in dark mode by using the deck foreground token instead of the card background token.
- Preserve Notion rollup progress and derived project fields during local cache updates after writeback.

### Security

- Restrict generated Obsidian links to the `Obsidian-Tech` vault and reject paths outside its root.
- Keep real earnings, customer names, platform credentials and local data snapshots out of Git.
