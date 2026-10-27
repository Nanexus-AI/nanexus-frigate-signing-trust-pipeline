"""Version-bound, authenticated exact-playback Live relay. No trust policy here."""

import asyncio
from datetime import datetime
from decimal import Decimal
import json
import os
import re
from urllib.parse import urlsplit

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from frigate.api.auth import allow_any_authenticated, require_camera_access
from frigate.version import VERSION

router = APIRouter()
HEADERS = {"Cache-Control": "no-store, private"}
FIELDS = {"status", "reason", "age_ms", "observed_at", "last_evidence_at", "session_id",
          "mapping_revision", "logical_camera", "rendition"}
REASONS = {"startup", "reconnect", "unsupported_signing_structure", "parser_uncertainty",
           "observation_discontinuity", "freshness_unconfigured", "no_media_observed",
           "observer_unavailable", "no_signing_evidence_in_window", "evidence_stale",
           "signing_evidence_overdue", "recent_signing_evidence", "invalid_request",
           "service_closed", "mapping_unavailable", "observation_stopped", "adapter_backlog",
           "transport_unavailable"}
IDENTITY = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


def unknown():
    return dict.fromkeys(FIELDS) | {"status": "UNKNOWN", "reason": "transport_unavailable"}


async def authorized_camera(request: Request, camera: str = Query(min_length=1, max_length=128, pattern=IDENTITY)):
    # Do not replace this with a deployment allowlist or caller-supplied role.
    await require_camera_access(camera_name=camera, request=request)
    if camera not in request.app.frigate_config.cameras:
        raise HTTPException(status_code=404, detail="Camera unavailable")
    return camera


def validate_result(data):
    if type(data) is not dict or set(data) != FIELDS:
        raise ValueError()
    if data["status"] not in ("ACTIVE", "DEGRADED", "UNKNOWN") or data["reason"] not in REASONS:
        raise ValueError()
    for key in ("logical_camera", "rendition", "session_id", "mapping_revision"):
        if data[key] is not None and (type(data[key]) is not str or not re.fullmatch(IDENTITY, data[key])):
            raise ValueError()
    for key in ("observed_at", "last_evidence_at"):
        if data[key] is not None:
            if type(data[key]) is not str or len(data[key]) > 40 or datetime.fromisoformat(data[key].replace("Z", "+00:00")).utcoffset() is None:
                raise ValueError()
    if data["age_ms"] is not None:
        age = data["age_ms"]
        if type(age) is not str or not re.fullmatch(r"[0-9]{1,20}(?:\.[0-9]{1,9})?", age) or not Decimal(age).is_finite():
            raise ValueError()
    if data["status"] != "UNKNOWN" and any(data[k] is None for k in FIELDS if k != "last_evidence_at"):
        raise ValueError()
    return data


async def fetch_status(camera, stream, context):
    if VERSION != "0.18.2-cfa08c5":
        raise ValueError()
    upstream = urlsplit(os.environ["NANEXUS_LIVE_UPSTREAM"])
    if (upstream.scheme != "http" or not upstream.hostname or upstream.username or upstream.password
            or upstream.path not in ("", "/") or upstream.query or upstream.fragment):
        raise ValueError()
    with open(os.environ["NANEXUS_LIVE_TOKEN_FILE"], "rb") as secret:
        token = secret.read(129).strip()
    if not re.fullmatch(rb"[A-Za-z0-9_-]{43,128}", token):
        raise ValueError()
    # No browser cookies, Authorization, identity headers, environment proxy or redirects.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1.5), trust_env=False,
                                     auto_decompress=False) as client:
        async with client.get(upstream.geturl().rstrip("/") + "/v1/live/status",
                              params={"camera": camera, "stream": stream, "context": context},
                              headers={"Authorization": "Bearer " + token.decode("ascii")},
                              allow_redirects=False) as response:
            if (response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity"
                    or response.content_type != "application/json"):
                raise ValueError()
            body = bytearray()
            async for chunk in response.content.iter_chunked(1024):
                body.extend(chunk)
                if len(body) > 4096:
                    raise ValueError()
    def constant(_):
        raise ValueError()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return validate_result(json.loads(body, parse_constant=constant, object_pairs_hook=pairs))


@router.get("/nanexus/live/status", dependencies=[Depends(allow_any_authenticated())])
async def live_status(request: Request, camera: str = Depends(authorized_camera),
                      stream: str = Query(min_length=1, max_length=128, pattern=IDENTITY),
                      context: str = Query(min_length=1, max_length=128, pattern=IDENTITY)):
    if (set(request.query_params) != {"camera", "stream", "context"}
            or any(len(request.query_params.getlist(k)) != 1 for k in request.query_params)):
        raise HTTPException(status_code=422, detail="Invalid playback request")
    try:
        result = await fetch_status(camera, stream, context)
    except (Exception, asyncio.TimeoutError):
        result = unknown()  # Never forward internal errors or cache prior ACTIVE.
    return JSONResponse(result, headers=HEADERS)
