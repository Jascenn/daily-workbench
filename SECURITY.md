# Security Policy

## Supported version

Security fixes are applied to the latest `main`.

## Report a vulnerability privately

Do not open a public issue for a vulnerability or attach credentials, real
earnings, customer names, or local data snapshots. Use GitHub private
vulnerability reporting from the repository Security tab. If that option is
unavailable, contact the repository owner through a private GitHub channel.

## In scope

- exposure of Notion or other platform credentials;
- a path that breaks the read-only boundary around external systems;
- a bypass of the local synchronization token;
- a bug that causes real data to be replaced by guest data, or the reverse;
- an unreviewed write-back from the local workbench to Notion.

## Safe reports

Use fixture data or a fresh guest-mode profile. Redact real records and local
absolute paths before sharing logs or screenshots.
