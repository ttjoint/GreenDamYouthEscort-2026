"""M2 stage 1's other half: firewall rules that refuse a listed host.

Stage 1 in ``docs/architecture.md`` is "domain / SNI only". A proxy sees the
host name; a firewall sees only addresses. The two are complementary, and this
module produces the *firewall* side: rules that drop traffic to the addresses a
listed host resolves to.

**Why build command lines instead of calling the API.** ``netsh advfirewall``
and ``New-NetFirewallRule`` are both scriptable, and building the argument list
as data means every rule can be asserted before anything is executed. The test
for "the rule names the right address" then needs no administrator and no
firewall. The execution itself is one ``subprocess`` call that the installer
performs, and it is the only part that needs elevation.

**Why every rule is tagged and reversible.** ``docs/architecture.md`` §M7 and the
journal module both assume uninstall can undo what install did. A firewall rule
that cannot be identified later is a rule that survives uninstall, which is
exactly the behaviour this project is a reaction to. Every rule therefore carries
a name prefix and a group, and :func:`remove_rules` removes by group.

**Why addresses and not host names.** ``netsh`` accepts no host names, and
Windows Firewall resolves nothing at rule-creation time. A rule listing an
address blocks that address only; a host that changes address escapes. That is a
real limitation of stage 1 and it is recorded here rather than papered over -
the proxy path (stage 2) is what actually enforces by name.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass

__all__ = [
    "RULE_PREFIX",
    "RULE_GROUP",
    "FirewallRule",
    "FirewallPlan",
    "rule_name_for",
    "build_add_rules",
    "build_add_rules_command",
    "build_remove_rules",
    "build_show_rules",
    "is_address",
    "NETSH",
]

#: Every rule this product creates starts with this. Removal matches on the
#: group, but the prefix makes an orphan obvious in the Windows Firewall UI.
RULE_PREFIX = "GreenDam2026-"

#: The group name. ``netsh`` can delete a whole group in one command, which is
#: what makes uninstall reliable even if the journal is lost.
RULE_GROUP = "GreenDam Youth Escort 2026"

#: The tool. Kept as a tuple so the builder can be tested without a shell.
NETSH = ("netsh", "advfirewall", "firewall")


@dataclass(frozen=True)
class FirewallRule:
    """One outbound block rule. Data, not a command line."""

    name: str
    remote_ip: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "remote_ip": self.remote_ip}


@dataclass(frozen=True)
class FirewallPlan:
    """Everything needed to apply or undo a set of rules."""

    rules: tuple[FirewallRule, ...] = ()
    #: Addresses that were rejected because they are not literal IPs.
    rejected: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.rules)

    def summary(self) -> dict[str, object]:
        return {
            "rules": [r.to_dict() for r in self.rules],
            "rejected": list(self.rejected),
            "count": len(self.rules),
        }


def is_address(text: str) -> bool:
    """True when ``text`` is a literal IPv4/IPv6 address *or network*.

    Used to reject host names before they reach ``netsh``: a rule containing a
    host name is silently ignored by Windows Firewall, so a filter that "created
    a rule" for ``evil.example`` would report success and block nothing. Failing
    loudly here is the whole point.

    **Networks count.** ``netsh`` accepts ``remoteip=10.0.0.0/8`` and
    ``remoteip=0.0.0.0/0``; an earlier version of this function used
    ``ipaddress.ip_address`` alone, which rejects anything with a prefix. The
    effect was that every CIDR was silently dropped into ``rejected`` - a
    subnet block reported as "39 rules and 1 problem" where the 1 problem was
    the whole point. Widening a block from one address to a range is a normal
    thing to want (see ``docs/firewall-policy.md``), so the check has to accept
    what the firewall accepts.
    """
    try:
        ipaddress.ip_network(text.strip().strip("[]"), strict=False)
    except ValueError:
        return False
    return True


def _normalise(text: str) -> str:
    """The canonical form of an address or network.

    Single addresses stay bare (``1.2.3.4``, not ``1.2.3.4/32``) because that is
    what ``netsh`` shows back and what the journal records; networks keep their
    prefix. ``strict=False`` means ``10.0.0.1/8`` is accepted and normalised to
    ``10.0.0.0/8`` rather than refused - a parent who types the wrong host part
    meant the network.
    """
    bare = text.strip().strip("[]")
    if "/" in bare:
        return str(ipaddress.ip_network(bare, strict=False))
    return str(ipaddress.ip_address(bare))


def rule_name_for(address: str) -> str:
    """A stable, filesystem- and shell-safe rule name for one address.

    Colons are replaced because IPv6 addresses contain them and ``netsh``
    argument parsing treats a bare ``:`` badly in some locales. The replacement
    is reversible enough for a human to read; the authoritative mapping is the
    journal, not the name.
    """
    safe = address.strip().strip("[]").replace(":", "-").replace("/", "_")
    return f"{RULE_PREFIX}{safe}"


def build_add_rules(addresses, *, direction: str = "out") -> FirewallPlan:
    """Turn addresses and networks into outbound block rules.

    Non-addresses are collected in ``rejected`` rather than raising: a list of
    forty addresses where one is a host name should produce thirty-nine rules and
    one reported problem, not zero rules and an exception. The caller logs the
    rejected ones.

    Duplicates are collapsed, because ``netsh`` refuses to add a rule whose name
    already exists and a duplicate would otherwise make the whole batch fail.

    A CIDR entry becomes one rule covering the whole network, which is what
    makes a "block everything outside these ranges" policy expressible as a
    handful of rules instead of a list of individual addresses.
    """
    seen: set[str] = set()
    rules: list[FirewallRule] = []
    rejected: list[str] = []

    for raw in addresses:
        text = str(raw).strip()
        if not text:
            continue
        if not is_address(text):
            rejected.append(text)
            continue
        normalised = _normalise(text)
        if normalised in seen:
            continue
        seen.add(normalised)
        rules.append(FirewallRule(name=rule_name_for(normalised),
                                  remote_ip=normalised))

    if direction not in {"out", "in"}:
        raise ValueError(f"direction must be 'out' or 'in', got {direction!r}")

    return FirewallPlan(rules=tuple(rules), rejected=tuple(rejected))


def build_add_rules_command(rule: FirewallRule, *,
                            direction: str = "out") -> tuple[str, ...]:
    """The ``netsh`` argument vector for adding one rule.

    An argument *vector*, never a shell string: an address containing a quote or
    an ampersand must not be able to become a second command. ``netsh`` is
    invoked with ``subprocess.run(argv)`` and no ``shell=True``.
    """
    return NETSH + (
        "add", "rule",
        f"name={rule.name}",
        f"dir={direction}",
        "action=block",
        f"remoteip={rule.remote_ip}",
        "protocol=any",
        "enable=yes",
        f"group={RULE_GROUP}",
        "profile=any",
    )


def build_remove_rules() -> tuple[str, ...]:
    """The ``netsh`` argument vector that removes every rule this product made.

    Deletes by *group*, not by individual name. A group delete succeeds even when
    the journal is missing or a rule name was written by an older version, which
    is the failure mode that matters: a firewall rule surviving uninstall.
    """
    return NETSH + ("delete", "rule", f"group={RULE_GROUP}")


def build_show_rules() -> tuple[str, ...]:
    """The ``netsh`` argument vector that lists what is currently installed.

    Used by the uninstaller to verify its own work, and by ``status`` to report
    whether rules are active.
    """
    return NETSH + ("show", "rule", f"name=all", f"group={RULE_GROUP}")
