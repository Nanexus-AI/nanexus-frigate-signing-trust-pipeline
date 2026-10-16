"""One explicit boundary for the supported Toolkit verify/inspect JSON profile."""

import json
import os
import re
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from ..contracts import VerificationResult
from ..evidence import Component
from .models import BackendObservation, HistoricalError


def run_tool(argv, limits):
    """No pipes/reader threads: bounded, monitored temporary stdout/stderr files.

    Trusted configured executables run in an owned POSIX process session. Time or
    output limits kill that group; raw diagnostics are not persisted in evidence.
    """
    if os.name != "posix":
        raise HistoricalError("BACKEND_UNAVAILABLE")
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            with subprocess.Popen([str(a) for a in argv], stdin=subprocess.DEVNULL,
                                  stdout=stdout, stderr=stderr, start_new_session=True) as process:
                deadline = time.monotonic() + limits.timeout_seconds
                while True:
                    code = process.poll()
                    if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > limits.max_tool_output:
                        if code is None:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        raise HistoricalError("RESOURCE_LIMIT")
                    if code is not None:
                        break
                    if time.monotonic() >= deadline:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        raise HistoricalError("BACKEND_TIMEOUT")
                    time.sleep(0.01)
                stdout.seek(0)
                return code, stdout.read(limits.max_tool_output + 1)
    except OSError as exc:
        raise HistoricalError("BACKEND_UNAVAILABLE") from exc


def parse_toolkit(verify, inspect, codes):
    """Fail closed on unmapped schemas, counters, exits or trust findings."""
    try:
        if (type(verify) is not dict or type(inspect) is not dict
                or inspect["verification"] != verify
                or verify["schema_version"] != "0.1" or inspect["schema_version"] != "0.1"
                or inspect["document_type"] != "media_signing_inspection"
                or verify["codec"] != "h264" or verify["media_signing"]["present"] is not True
                or verify["certificate_status"] != "ok"
                or verify["public_key_has_changed"] is not False
                or verify["source_authenticity"] != "not_established"):
            raise ValueError("unsupported Toolkit profile")
        findings = verify["findings"]
        if type(findings) is not list or any(type(f) is not dict or f["code"] not in
                                           ("SIGNING_KEY_PROVENANCE_OK", "AUTH_NOT_OK") for f in findings):
            raise ValueError("unsupported findings")
        if not any(f["code"] == "SIGNING_KEY_PROVENANCE_OK" for f in findings):
            raise ValueError("missing supported trust finding")
        counts = inspect["accumulated_validation"]
        pending = []
        for unit in ("frames", "nalus"):
            received, validated, remaining = (counts["number_of_" + key + "_" + unit]
                                               for key in ("received", "validated", "pending"))
            if any(type(v) is not int or v < 0 for v in (received, validated, remaining)) or validated + remaining != received:
                raise ValueError("inconsistent counters")
            pending.append(remaining)
        state = (verify["overall"], verify["signature_integrity"], verify["continuity"],
                 verify["verification_completeness"])
        if state[0] != "INVALID" and any(f["code"] == "AUTH_NOT_OK" for f in findings):
            raise ValueError("contradictory integrity finding")
        valid = state == ("VALID", "ok", "intact", "complete") and pending == [0, 0] and codes == (0, 0)
        partial = state == ("PARTIAL", "ok", "intact", "incomplete") and all(p > 0 for p in pending) and codes == (4, 4)
        invalid = (state[:3] == ("INVALID", "not_ok", "broken") and codes == (1, 1)
                   and ((state[3] == "complete" and pending == [0, 0])
                        or (state[3] == "incomplete" and all(p > 0 for p in pending))))
        if not (valid or partial or invalid):
            raise ValueError("unmapped Toolkit outcome")
        return BackendObservation(VerificationResult(state[0]), *state[1:], *pending)
    except (KeyError, ValueError, TypeError) as exc:
        raise HistoricalError("BACKEND_UNSUPPORTED") from exc


class ToolkitBackend:
    """Deployment supplies executables and public trust configuration, never signer keys."""

    def __init__(self, executable: Path, trust_anchor: Path, version: str, *, retain_reports=True):
        self.executable = Path(executable)
        self.trust_anchor = Path(trust_anchor)
        self.component = Component("nanexus-toolkit", version)
        self.retain_reports = retain_reports

    def verify(self, annex_b, limits):
        documents, codes, raw_reports = [], [], []
        for operation in ("verify", "inspect"):
            code, output = run_tool([self.executable, operation, "--codec", "h264", "--ca",
                                     self.trust_anchor, "--json", annex_b], limits)
            try:
                document = json.loads(output)
            except (ValueError, RecursionError) as exc:
                raise HistoricalError("BACKEND_UNSUPPORTED") from exc
            documents.append(document); codes.append(code); raw_reports.append(output)
        observation = parse_toolkit(*documents, tuple(codes))
        if self.retain_reports:
            # Only supported JSON reports; command lines/stderr/probe paths never leave runtime.
            artifacts = {}
            for name, document, payload in zip(("verify", "inspect"), documents, raw_reports):
                # Refuse leaking configured physical locations from a diagnostic string.
                if any(str(p).encode() in payload for p in (self.executable, self.trust_anchor, annex_b)):
                    raise HistoricalError("BACKEND_UNSUPPORTED")
                pending = [document]
                while pending:
                    value = pending.pop()
                    if isinstance(value, str) and re.search(r"(?:^|\s)(?:/[^\s]+|[A-Za-z]:\\)", value):
                        raise HistoricalError("BACKEND_UNSUPPORTED")
                    if isinstance(value, dict):
                        for key, item in value.items():
                            pending.extend((key, item))
                    elif isinstance(value, list):
                        pending.extend(value)
                artifacts[name + ".json"] = ("backend-report", payload)
            from dataclasses import replace
            observation = replace(observation, artifacts=artifacts)
        return observation
