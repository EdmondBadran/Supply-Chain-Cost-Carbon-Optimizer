"""How far freight travels on a route, for a given transport mode.

A route stores one straight-line distance, which belongs to its two ends and
nothing else. What a particular mode would actually cover between those ends
is worked out here, every time, from that straight line and the mode being
asked about. Nothing stores a modal distance, so a candidate mode can never
pick up the distance of the mode a route happens to run on today, and one
route can never pick up another's.

Today the modal distance is the straight line times a screening multiplier
from factors.CIRCUITY. A real road, rail or sea router replaces `by_mode`
without anything that calls it having to change.
"""

from . import factors, geo


def straight_km(origin, dest):
    """Great-circle distance between two (lat, lon) points."""
    return geo.distance_km(origin[0], origin[1], dest[0], dest[1])


def by_mode(straight, mode):
    """Screening estimate of the distance `mode` travels over a route whose
    ends are `straight` km apart."""
    return straight * factors.circuity(mode)
