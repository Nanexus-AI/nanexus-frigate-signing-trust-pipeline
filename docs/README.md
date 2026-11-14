# Documentation

## Current operator guides

- [Source-only build and installation](frigate-source-build.md) — the single current installation procedure for Frigate **0.18.2-cfa08c5**, Linux amd64.
- [Support matrix](support-matrix.md) — supported behavior, limitations and recorded validation observations.
- [Operator acceptance procedure](frigate-e2e-acceptance.md) — reusable checks for a separate isolated installation, not a claim of production qualification.
- [Source licensing and distribution](source-license-boundaries.md) — portion-specific licenses, notices and generated-output obligations.

## Developer references

- [Live core](live.md) — structural evidence, receipt policy, session/mapping identities and observer API.
- [Historical verification](historical.md) — current window semantics followed by the separate offline service contract.
- [Recorded evidence](evidence-format.md) — standalone receipt/recognition API, not current-window export functionality.
- [Focused developer checks](frigate-source-build.md#developer-checks-and-test-portability) — explicitly selected offline tests and environment-bound test exclusions.

## Historical architecture references

These documents describe the older **Frigate 0.18.0** foundation and are not installation alternatives or current feature promises:

- [Companion foundation](integration.md).
- [Private Live relay](live-transport.md).
- [Durable Review/Export job transport](historical-transport.md).

The superseded private-install and private-image procedures are retained in Git history rather than the current tree. Their required source helpers remain available where the current Builder imports them.
