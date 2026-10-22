"""Replay flows from a file instead of from a network.

This is the substitute for a real proxy in tests and in the CLI. It reads a
JSON list of flow descriptions, plans each one through
:mod:`green_dam.transport`, and feeds the resulting request to a
:class:`~green_dam.filter.Filter`.

Why this exists instead of an integration test against a live proxy:

* no administrator rights, no listening sockets, no root CA
* fully deterministic and repeatable
* it can construct cases a live network makes hard to produce - a pinned
  certificate, a QUIC flow, a body the stage could not have decrypted

The last point is the important one: a live test proves the proxy works on the
happy path; this proves the *degradation* logic is right, which is where the
architecture's guarantees actually live.

The format
----------
::

    [
      {"host": "example.com", "port": 80, "protocol": "http",
       "body": "GET http://example.com/ HTTP/1.1"},
      {"host": "example.com", "port": 443, "protocol": "https",
       "pinned": true}
    ]

Unknown keys are rejected rather than ignored: a typo in a fixture would
otherwise silently produce a different test than the author intended.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .filter import Filter
from .transport import Flow, Plan, plan

#: Keys a flow record may contain. Anything else is a mistake.
FLOW_KEYS = frozenset({"host", "port", "protocol", "pinned", "body", "is_image"})


class FlowFileError(ValueError):
    """Raised when a flow file is malformed."""


def parse_flows(payload: object) -> list[Flow]:
    """Turn decoded JSON into :class:`Flow` objects, validating strictly."""
    if not isinstance(payload, list):
        raise FlowFileError(
            f"flow file must contain a JSON array, got {type(payload).__name__}")

    flows: list[Flow] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise FlowFileError(f"flow {index} is not an object")
        unknown = set(item) - FLOW_KEYS
        if unknown:
            raise FlowFileError(
                f"flow {index} has unknown keys {sorted(unknown)}; "
                f"allowed: {sorted(FLOW_KEYS)}")
        for required in ("host", "port"):
            if required not in item:
                raise FlowFileError(f"flow {index} is missing {required!r}")
        port = item["port"]
        if not isinstance(port, int) or not 0 < port < 65536:
            raise FlowFileError(f"flow {index} has an invalid port: {port!r}")
        flows.append(Flow(
            host=str(item["host"]),
            port=port,
            protocol=item.get("protocol", "http"),
            pinned=bool(item.get("pinned", False)),
            body=item.get("body"),
            is_image=bool(item.get("is_image", False)),
        ))
    return flows


def load_flows(path: Path) -> list[Flow]:
    """Read and validate a flow file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FlowFileError(f"{path} is not valid JSON: {exc}") from exc
    return parse_flows(payload)


@dataclass(frozen=True)
class ReplayResult:
    """One flow, the plan it produced, and the assessment it led to."""

    flow: Flow
    plan: Plan
    #: ``None`` when the stage could not produce a request worth judging.
    assessment: object | None

    @property
    def limits(self) -> tuple[str, ...]:
        return self.plan.limits


def replay(flows: list[Flow], *, filter_obj: Filter, ca_installed: bool = False,
           image_supported: bool = True) -> Iterator[ReplayResult]:
    """Run each flow through the planner and the filter, in order."""
    for flow in flows:
        planned = plan(flow, ca_installed=ca_installed,
                       image_supported=image_supported)
        assessment = filter_obj.check(request=planned.request)
        yield ReplayResult(flow=flow, plan=planned, assessment=assessment)


def replay_file(path: Path, *, filter_obj: Filter, ca_installed: bool = False,
                image_supported: bool = True) -> list[ReplayResult]:
    """Convenience wrapper: load a file and replay it."""
    return list(replay(load_flows(path), filter_obj=filter_obj,
                       ca_installed=ca_installed,
                       image_supported=image_supported))
