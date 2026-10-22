"""Four-stage interception skeleton, with the transport abstracted away.

``docs/architecture.md`` §M2 says content inspection needs plaintext, so
interception is staged and each stage is independently useful:

===========  ======================================  ==========  =============
stage        mechanism                                needs CA    sees content
===========  ======================================  ==========  =============
1            domain / SNI only                        no          no
2            plain HTTP, transparent proxy            no          yes
3            HTTPS, local MITM proxy                  yes         yes
4            image / OCR on the stage-2/3 body        inherits    yes
===========  ======================================  ==========  =============

What this module is, and is not
-------------------------------
It is the **decision skeleton**: given a flow, which stage can judge it, what
does that stage get to see, and what does it do when it cannot see enough. It
performs no networking. The actual proxy and firewall are the host's job
(``.NET 8``, Windows-only), and they will call :func:`plan` to learn what they
are expected to supply.

That split is why this is testable without a network at all, and it is the same
split the architecture already makes between engines (Python, portable) and host
(.NET, Windows).

Why stages degrade rather than fail
-----------------------------------
A pinned certificate cannot be decrypted. QUIC is UDP and is not proxied. A
TLS handshake may fail for reasons that have nothing to do with policy. In every
one of those cases the right answer is *not* "allow silently" - it is "act on
what is visible, and report the limit". :class:`Plan` carries ``limits`` for
exactly that reason, so the caller can surface "checked the host only" instead
of implying a full inspection.

This is the same distinction the rest of the project keeps making: not looking
is not the same as finding nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

from .intercept import HTTP_PORTS, Request

Protocol = Literal["http", "https", "quic", "other"]


class Stage(IntEnum):
    """Which interception stage applies. Higher reaches more, costs more."""

    HOST_ONLY = 1
    PLAIN_HTTP = 2
    HTTPS_MITM = 3
    CONTENT_IMAGE = 4


#: Human-readable reason a stage cannot be used. Stable tokens, used in logs.
LIMIT_PINNED = "certificate_pinned"
LIMIT_QUIC = "quic_not_proxied"
LIMIT_NO_CA = "no_ca_installed"
LIMIT_NO_BODY = "body_unavailable"
LIMIT_NO_IMAGE = "image_unsupported"
LIMIT_UNKNOWN_PROTOCOL = "unknown_protocol"


@dataclass(frozen=True)
class Flow:
    """One connection as the host can describe it *before* any decryption.

    Everything here is available to a firewall or a proxy listener without
    touching the payload, which is why stage 1 can always act.
    """

    host: str
    port: int
    protocol: Protocol = "http"
    #: True when the client pins the server certificate (detected by the host
    #: after a failed interception, or configured out of band).
    pinned: bool = False
    #: Decoded body, when the host managed to obtain it.
    body: str | None = None
    #: True when the body is a raster image (stage 4 territory).
    is_image: bool = False


@dataclass(frozen=True)
class Plan:
    """What the host should do for one flow, and what it cannot do.

    ``stage`` is the furthest stage that can be used. ``limits`` lists every
    reason a more capable stage was unavailable, so the caller can report a
    partial inspection honestly rather than as a clean result.
    """

    stage: Stage
    #: Fields to put into the :class:`~green_dam.intercept.Request`.
    request: Request
    limits: tuple[str, ...] = ()
    #: True when the plan can see the body at all.
    sees_content: bool = False
    #: True when the plan can judge images.
    sees_images: bool = False

    @property
    def partial(self) -> bool:
        """True when some capability was unavailable for this flow."""
        return bool(self.limits)


def plan(flow: Flow, *, ca_installed: bool = False,
         image_supported: bool = True) -> Plan:
    """Decide which stage applies to ``flow`` and build its request.

    The rules, in order:

    1. QUIC is UDP and never proxied, so it can only be judged by host.
    2. Plain HTTP is always readable - no CA involved.
    3. HTTPS needs a CA *and* a client that does not pin the certificate.
    4. Image judging applies on top of any stage that produced a body.

    ``ca_installed`` defaults to False because assuming a CA is present would
    silently claim a capability the host may not have.
    """
    limits: list[str] = []

    # -- stage 1 always works: the host and port are visible to any listener.
    stage = Stage.HOST_ONLY
    sees_content = False

    if flow.protocol == "quic":
        limits.append(LIMIT_QUIC)
    elif flow.protocol == "http":
        stage = Stage.PLAIN_HTTP
        sees_content = True
    elif flow.protocol == "https":
        if flow.pinned:
            limits.append(LIMIT_PINNED)
        elif not ca_installed:
            limits.append(LIMIT_NO_CA)
        else:
            stage = Stage.HTTPS_MITM
            sees_content = True
    else:
        limits.append(LIMIT_UNKNOWN_PROTOCOL)

    # -- stage 4 rides on top of a body, whichever stage produced it.
    sees_images = False
    if sees_content and flow.body is not None:
        if flow.is_image:
            if image_supported:
                stage = Stage.CONTENT_IMAGE
                sees_images = True
            else:
                limits.append(LIMIT_NO_IMAGE)
    elif sees_content and flow.is_image:
        limits.append(LIMIT_NO_BODY)

    request = Request(
        direction="outbound",
        port=flow.port,
        host=flow.host,
        # Only hand the body over when the stage can actually see it. Passing a
        # body the stage could not have decrypted would let a caller believe a
        # check happened that did not.
        body=flow.body if sees_content else None,
    )

    return Plan(stage=stage, request=request, limits=tuple(limits),
                sees_content=sees_content, sees_images=sees_images)


def keyword_ports() -> frozenset[int]:
    """Ports on which inbound keyword filtering is meaningful (stage 2/3)."""
    return HTTP_PORTS


def describe(plan_obj: Plan) -> dict:
    """Serialisable summary for the log. Contains no host and no body."""
    return {
        "stage": int(plan_obj.stage),
        "sees_content": plan_obj.sees_content,
        "sees_images": plan_obj.sees_images,
        "limits": list(plan_obj.limits),
        "partial": plan_obj.partial,
    }
