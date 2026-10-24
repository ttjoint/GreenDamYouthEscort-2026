"""From a parsed request to "forward" or "block", with no sockets.

This is where the host meets the engine. The engine supplies
:func:`green_dam.transport.plan` (which stage can judge this flow) and
``Filter.check_request`` (does it block). This module supplies the parts neither
can: what the host knows about the client and the upstream, and what to do when
the two disagree.

Three rules encoded here, each of which is a decision rather than an obvious
consequence:

**A block never opens the upstream connection.** Checking before connecting
means a blocked host is never contacted. A proxy that connects first and asks
afterwards has already told the blocked server who is asking, and has already
paid the latency.

**A partial inspection is reported, not upgraded.** When ``plan`` says the flow
is host-only, the decision is a host-only decision. It is never recorded as a
clean result, and never silently promoted because the body happened to be
readable.

**The block page says what happened, not what matched.** It names the reason
token (``host_listed``) and never the matched term. A block page that echoes the
rule turns the filter into an oracle: a child can binary-search the wordlist by
reading the page.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..proxy.message import RequestMessage

__all__ = [
    "Verdict",
    "Outcome",
    "decide",
    "block_page",
    "BLOCK_STATUS",
    "FORWARD",
    "BLOCK",
]

#: 403 rather than a reset: a stage-2 proxy can explain itself, unlike a
#: firewall rule. The engine's ``BLOCK_ERRNO`` (10054) is what the *original*
#: surfaced to the blocked program; here the blocked party is a browser and a
#: page is more useful than a connection reset.
BLOCK_STATUS = "403 Forbidden"

FORWARD = "forward"
BLOCK = "block"


@dataclass(frozen=True)
class Outcome:
    """What the server should do with one request, and why.

    ``plan`` is kept whole rather than flattened into fields: the limits are the
    evidence that an inspection was partial, and the log needs all of them.
    """

    action: str
    reason: str
    #: The engine's plan for this flow, with its ``limits``.
    plan: object
    #: The engine's decision, when one was made.
    decision: object | None = None

    @property
    def blocked(self) -> bool:
        return self.action == BLOCK

    @property
    def partial(self) -> bool:
        """True when the plan could not see everything it wanted to."""
        return bool(getattr(self.plan, "limits", ()))

    def to_dict(self) -> dict[str, object]:
        """Loggable summary. No host, no path, no body."""
        return {
            "action": self.action,
            "reason": self.reason,
            "stage": int(getattr(self.plan, "stage", 0)) if self.plan else 0,
            "limits": list(getattr(self.plan, "limits", ())),
            "partial": self.partial,
        }


def _flow_for(message: RequestMessage, *, tls: bool = False,
              image_supported: bool = True):
    """Describe the flow the way ``transport.plan`` expects it.

    ``protocol`` is derived from how the request arrived, not from the target:
    a plain-HTTP request whose target happens to start with ``https://`` was
    still delivered in the clear, and claiming otherwise would have the plan
    skip a body it can actually read.
    """
    from green_dam.transport import Flow       # noqa: PLC0415 - lazy by design

    protocol = "https" if tls else "http"
    return Flow(host=message.host or "",
                port=message.port or (443 if tls else 80),
                protocol=protocol,
                body=message.body,
                is_image=False)


def decide(filter_, message: RequestMessage, *, tls: bool = False,
           process: str | None = None,
           image_supported: bool = True) -> Outcome:
    """Decide one parsed request. Pure: no I/O, no logging, no clock.

    ``process`` is the client's executable name when the host can attribute the
    connection. Attribution is best-effort - on Windows it needs a connection
    table lookup that may race - so ``None`` means "unknown", and the process
    stage simply does not fire. That is the same degrade-not-fail rule the
    engine already uses for every other unavailable input.
    """
    from green_dam.transport import plan as plan_flow   # noqa: PLC0415

    flow = _flow_for(message, tls=tls, image_supported=image_supported)
    flow_plan = plan_flow(flow)

    request = flow_plan.request
    if process:
        # ``Request`` is frozen, so rebuild rather than mutate. The process is
        # the only field the transport could not supply.
        from green_dam.intercept import Request          # noqa: PLC0415

        request = Request(direction="outbound", process=process,
                          port=request.port, host=request.host,
                          body=request.body)

    assessment = filter_.check_request(request)

    if assessment.blocked:
        return Outcome(action=BLOCK,
                       reason=str(assessment.reason or "network_block"),
                       plan=flow_plan,
                       decision=getattr(assessment, "network", None))

    # No block. If the plan could not see the body, the result is "not looked
    # at", which must not be reported as "clean". The action stays forward -
    # this product does not block on absence of evidence - but the reason says
    # so, and the limits travel with it into the log.
    reason = "allowed" if not flow_plan.limits else "allowed_partial"
    return Outcome(action=FORWARD, reason=reason, plan=flow_plan,
                   decision=getattr(assessment, "network", None))


def _html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


#: Reason tokens -> the sentence a parent or child reads. Only tokens that the
#: engine actually emits appear here; an unknown token falls back to a generic
#: sentence rather than leaking the token, because a token is an internal name.
_REASON_TEXT = {
    "host_listed": "该网站的域名在禁止访问的名单中。",
    "address_listed": "该网站的地址与端口在禁止访问的名单中。",
    "watched_process": "发起访问的程序被设置为不允许联网。",
    "url_in_body": "请求内容中包含被禁止的网址。",
    "keyword_rule": "页面内容命中关键词过滤规则。",
    "network_block": "该访问被策略阻止。",
}


def block_page(outcome: Outcome, message: RequestMessage | None = None) -> bytes:
    """The page served instead of the blocked resource.

    Deliberately minimal: a status, a reason sentence, and a line naming the
    product. It does **not** include the matched term, the matched rule, the
    wordlist filename, or the URL that triggered it. Each of those would let the
    reader reverse-engineer the rule set by requesting URLs and reading replies.
    """
    sentence = _REASON_TEXT.get(outcome.reason,
                                "该访问被策略阻止。")
    limits = getattr(outcome.plan, "limits", ()) if outcome.plan is not None else ()
    note = ""
    if limits:
        # Naming the limit is not a leak: it describes our capability, not the
        # rule. A parent seeing "HTTPS 未解密" learns why a site got through,
        # which is the single most useful thing this page can say.
        note = ("<p class=\"limit\">本次检查范围受限（{}），"
                "未能完整检查页面内容。</p>").format(
                    _html_escape(", ".join(limits)))

    body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>访问被阻止</title>
<style>
 body {{ font-family: system-ui, "Microsoft YaHei", sans-serif;
        margin: 4rem auto; max-width: 34rem; line-height: 1.7; color: #222; }}
 h1 {{ font-size: 1.4rem; }}
 .reason {{ background: #f6f6f6; border-left: 4px solid #c00;
            padding: .75rem 1rem; }}
 .limit {{ color: #666; font-size: .9rem; }}
 footer {{ color: #999; font-size: .8rem; margin-top: 3rem; }}
</style>
</head>
<body>
<h1>访问被阻止</h1>
<p class="reason">{_html_escape(sentence)}</p>
{note}
<p>如需访问，请联系家长在控制台调整设置。</p>
<footer>Green Dam Youth Escort 2026</footer>
</body>
</html>
"""
    return body.encode("utf-8")
