"""M2 stages 1-3: the host's network interception.

The engine's :mod:`green_dam.transport` decides *which stage can judge a flow*
and what that stage is allowed to see. It performs no networking by design. This
package is the other half: real sockets, real upstreams, and a proxy that asks
``transport.plan`` what it may do before doing it.

Split into three layers, in the order a request flows through them:

``message``  HTTP/1.x parsing. Pure bytes, no sockets. Testable exhaustively.
``policy``   "given this parsed message and the engine, forward or block?"
             Pure. Returns a plan plus a decision. No sockets.
``server``   The accept loop and the byte shuffling. The only part that needs a
             network, and therefore the only part whose tests bind a port.

Keeping the first two layers socket-free is not tidiness: it means the
interesting questions - does a pinned certificate downgrade to host-only, does a
blocked host stop the upstream connection from ever being opened, does the block
page leak the matched term - are answered by tests that cannot be flaky.
"""
from __future__ import annotations

__all__ = ["message", "policy", "server"]
