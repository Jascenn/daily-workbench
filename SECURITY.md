# Security Policy

## Supported version

This is a private development mirror. Security fixes are applied only to the
latest `main`.

## Report a vulnerability privately

Do not open a public issue for a vulnerability or include credentials, real
earnings, customer records, or local snapshots. Report privately to the
repository owner through GitHub.

## In scope

- leakage of real local data or platform credentials into Git;
- a path that breaks the read-only boundary around external systems;
- a bypass of the local synchronization token;
- accidental publication of private development data.

## Safe reports

Use fixture data and redact all personal and financial fields. This repository
must never contain production credentials or real data snapshots.
