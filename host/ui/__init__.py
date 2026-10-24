"""The parent console (M9 / K3).

A local web page served on the loopback interface, opened in whatever window the
machine can provide. Three reasons for a web UI over a native toolkit:

* the same HTML renders in WebView2, in an Edge ``--app`` window, and in a normal
  browser tab, so the console works even where ``pywebview`` is not installed;
* the markup is testable as data, and the data layer (:mod:`host.ui.data`) is
  testable without any GUI at all;
* the project already ships an extension written in JS, so the alternative - a
  second UI language - buys nothing.

The console is deliberately **not** a remote administration interface. It binds
to ``127.0.0.1`` only, requires a per-launch token, and refuses cross-origin
requests. See :mod:`host.ui.server` for why each of those is load-bearing.
"""
from __future__ import annotations

__all__ = ["data", "server", "launch"]
