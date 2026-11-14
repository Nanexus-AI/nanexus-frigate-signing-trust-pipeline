# Historical Frigate 0.18.0 Review/Export job transport

> **Historical reference — superseded integration profile.** This document describes Frigate 0.18.0, not the supported 0.18.2 M1 installation. Retained module/transport design details are useful to maintainers; statements below about persistence, evidence downloads, Review/Export or future work apply to that older profile. They do not describe features delivered by the current Historical window. Use the [source-build guide](frigate-source-build.md) for installation and [current Historical window reference](historical.md#current-frigate-0182-window) for current behavior.

This extends the separate Frigate v0.18.0 / `0.18.0-77a66e7` patch for the desktop single-camera Review/Export workflow. Historical verification concerns retained signed recording material, never the produced export artifact. D1 job, result, staging, execution and portable evidence contracts are unchanged.

The browser calls same-origin Frigate. Existing user authentication, CSRF behavior and camera authorization run before companion access. Frigate uses the fixed private deployment upstream and service-token file already configured for Live; browser cookies, JWTs and identity headers are not forwarded. The companion has no browser endpoint or CORS interface.

| Private companion operation | Frigate route under `/api/nanexus/historical` |
| --- | --- |
| POST `/v1/historical/jobs?camera=…` | POST `/jobs?camera=…` |
| GET `/v1/historical/jobs/{job}?camera=…` | GET `/jobs/{job}?camera=…` |
| GET `/v1/historical/jobs/{job}/evidence/{evidence}?camera=…` | GET `/jobs/{job}/evidence/{evidence}?camera=…` |
| GET the preceding evidence route plus `/download` | GET the preceding evidence route plus `/download` |

Submission accepts only `start` and `end` decimal Unix-second strings, preserving the existing half-open interval contract. Job and evidence identities are opaque references, not authorization tokens. Each lookup checks the selected authorized camera against the companion's durable job camera/range; evidence must match that job's stored evidence identity. Caller-provided filesystem paths, upstreams and extra query/body fields are rejected.

Submission is bounded to 1 KiB. Sanitized JSON responses are bounded to 32 KiB; evidence ZIP delivery is capped at 8 MiB with fixed attachment/content-type/nosniff headers. Larger bundles are unavailable through this desktop download route. Existing D1 evidence assembly/recognition limits still apply internally before this delivery cap. Only one evidence download assembly runs at a time, bounding the larger internal memory footprint independently of connection count. The private listener retains eight connection slots, a one-second socket timeout and two-second connection deadline. Frigate relay requests have a 1.5-second total timeout. Deployments must retain the existing private-network/service-token boundary; neither listener is an installation package.

A successful POST returns the durable job and is never automatically retried. A lost submission response can leave an accepted job whose ID the browser did not receive; the UI reports uncertainty, and an explicit subsequent submission may create another execution. GET/refresh/evidence recognition never rerun Toolkit. Interrupted and failed jobs retain their lifecycle state; they do not become cryptographic INVALID.

Only whitelisted lifecycle, result axes, requested range, actual context and evidence identity are exposed. Raw backend detail, staging paths and OS diagnostics are omitted. Unavailable/malformed transport is a service error, separate from a completed UNKNOWN media result. Historical failures do not block or rewrite native export results.

The browser uses existing per-user Frigate IndexedDB for a bounded latest camera/range-to-export/job/evidence association, with companion-owned job/evidence state authoritative. Existing native export IDs and their camera-authorized job/completed-export resources remain independent. No generic REST proxy, server association database, new authentication system or automatic verification scheduler is introduced.
