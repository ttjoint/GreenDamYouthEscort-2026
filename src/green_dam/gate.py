"""The filter's on/off switches, as one object.

Why this is its own module
--------------------------
``sysdat`` slots 2, 10 and 13 decide whether each path runs at all. That logic
used to live as three properties and a private method inside ``Filter``, mixed
in with the code that actually judged things. It is a separate concern: it
answers "should this path run", never "what does it find".

Keeping it here has three benefits:

* one place to change when a new slot turns out to be a switch
* testable without building a whole filter
* :class:`Gate` can be handed to a caller that only needs the switches, e.g. the
  console showing "filtering is off" without instantiating the engine

What a gate is not
------------------
A gate never produces ``allow``. It produces a *reason* that a path was skipped.
The distinction is the core behavioural correction this project makes against the
original product, which reported "nothing found" whether it had looked or not.

The slot numbers and their meanings
-----------------------------------
========  =============================  ==================================
slot      name                           effect when non-zero
========  =============================  ==================================
2         ``disable_filtering``          every path is skipped
10        ``disable_keyword_filter``     the text path is skipped
13        ``enable_image_filter``        image path runs only when non-zero
5         ``image_filter_level``         must also be non-zero for images
========  =============================  ==================================

Slot 13 is inverted relative to 2 and 10: it *enables* rather than disables.
That asymmetry is in the shipped data, not a choice made here.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import sysdat

#: Reason tokens. Stable strings, used in logs and asserted in tests.
DISABLED_ALL = "legacy_filtering_disabled"
DISABLED_KEYWORD = "legacy_keyword_filter_disabled"
DISABLED_IMAGE = "image_filter_disabled"

#: Slots this module interprets. Anything else in the policy array is not a
#: switch as far as the engine is concerned.
SLOT_ALL = 2
SLOT_KEYWORD = 10
SLOT_IMAGE = 13
SLOT_IMAGE_LEVEL = 5


@dataclass(frozen=True)
class Gate:
    """Answers "may this path run?" for one policy array.

    Construct from an array, or use :attr:`OPEN` for a filter with no policy at
    all - in which case every path that is off by default stays off, and the
    text path (which is not policy-controlled in the original) stays on.
    """

    #: None means no policy array was loaded.
    legacy: sysdat.PolicyArray | None = None

    # -- individual switches ----------------------------------------------

    @property
    def filtering_disabled(self) -> bool:
        """Slot 2: everything off."""
        return self.legacy is not None and self.legacy.filtering_disabled

    @property
    def keyword_filter_disabled(self) -> bool:
        """Slot 10: the text path off."""
        return self.legacy is not None and self.legacy.keyword_filter_disabled

    @property
    def image_filter_enabled(self) -> bool:
        """Slot 13 *and* slot 5: images are only judged when both are set.

        No policy array means no image filtering. "Not configured" must not read
        as "enabled" - that would silently claim a capability the host may not
        have, the same mistake the four-stage planner avoids.
        """
        return self.legacy is not None and self.legacy.image_filter_enabled

    # -- the question paths actually ask ----------------------------------

    def reason(self, *, keyword: bool = False, image: bool = False) -> str | None:
        """Why a path may not run, or ``None`` when it may.

        ``keyword`` adds slot 10; ``image`` adds slot 13. Slot 2 gates
        everything and is checked first, so its reason wins when several apply -
        the same first-hit-wins ordering the interception chain uses.
        """
        if self.filtering_disabled:
            return DISABLED_ALL
        if keyword and self.keyword_filter_disabled:
            return DISABLED_KEYWORD
        if image and not self.image_filter_enabled:
            return DISABLED_IMAGE
        return None

    def allows(self, *, keyword: bool = False, image: bool = False) -> bool:
        """Converse of :meth:`reason`, for readability at call sites."""
        return self.reason(keyword=keyword, image=image) is None

    # -- introspection ----------------------------------------------------

    def summary(self) -> dict[str, object]:
        """Switches only - no policy values beyond the ones interpreted here."""
        return {
            "filtering_disabled": self.filtering_disabled,
            "keyword_filter_disabled": self.keyword_filter_disabled,
            "image_filter_enabled": self.image_filter_enabled,
        }


#: A gate for a filter with no policy array loaded.
OPEN = Gate(legacy=None)


def from_policy(array: sysdat.PolicyArray | None) -> Gate:
    """Build a gate from a policy array (or None)."""
    return Gate(legacy=array)
