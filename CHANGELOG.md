# Changelog

## Unreleased — Post-M1 maintenance fixes

### Live Reader Recovery

- Recover from stale-generation ffmpeg readers that could leave Live status stuck at startup.
- Add bounded startup/no-byte and RTSP recovery, child cleanup and restart backoff.
- Preserve Live signing freshness and ACTIVE / DEGRADED / UNKNOWN semantics; missing signatures alone do not restart readers.

### Historical Local Timezone

- Follow Frigate’s configured `ui.timezone`, or the browser timezone when unset.
- Use one display timezone for the calendar, recording-day indicators, query input and result labels while backend verification retains absolute Unix timestamps.
- Reject nonexistent or ambiguous daylight-saving times instead of silently converting them; retain the 1,800-second actual-duration limit.

Deployment validation of these maintenance fixes is pending.
