"""Cost and emission factors per transport mode.

Emission figures are kg CO2e per tonne-km, in line with the ranges published
by DEFRA and the GLEC framework. Cost figures are USD per tonne-km and are
far more variable in reality, so they are treated as defaults a user can
override per lane rather than as ground truth.
"""

MODES = ("road", "rail", "sea", "air")

# Sources, checked 2026-09-11. Air matches the GLEC v3.2 long-haul figure of
# 0.608 almost exactly. Rail sits between the GLEC EU electric figure of 0.0108
# and the diesel figure of 0.0307, which is what a mixed traction assumption
# should look like. Sea is inside the GLEC bulk carrier range of 0.0031 to
# 0.0312, though near its efficient end.
#
# Road was 0.062 until this date, which was about 40 percent below every
# published figure that could be found: DEFRA 2024 puts an average laden
# articulated HGV at roughly 0.101, and GLEC v3.2 puts articulated HDVs between
# 0.074 and 0.107. Since the method page claims these follow the DEFRA and GLEC
# ranges, a factor outside those ranges was a claim the tool did not meet. It
# now uses the DEFRA average laden figure. The effect is small on both samples,
# which are air dominated, but it raises the road to rail carbon ratio from
# 2.8x to the 4.6x the published factors actually imply.
EMISSION_FACTORS = {
    "road": 0.101,
    "rail": 0.022,
    "sea": 0.008,
    "air": 0.602,
}

# Air is the one worth stating a source for, because getting it wrong throws
# every result off. Long haul general air cargo runs roughly 2 to 5 USD per kg,
# so about 3,000 USD a tonne over a 16,000 km route, which is 0.19 per
# tonne-km. That puts air about 1.6 times road and 24 times sea on cost. The
# case for moving off it is carbon rather than money: on emissions it is about
# 10 times road and 75 times sea.
COST_FACTORS = {
    "road": 0.12,
    "rail": 0.04,
    "sea": 0.008,
    "air": 0.19,
}

# Packaging emissions and cost are charged per order rather than per tonne-km,
# since they scale with how many parcels go out, not how far they travel.
PACKAGING_KG_CO2E_PER_ORDER = 0.45
PACKAGING_COST_PER_ORDER = 0.85

# A return costs the outbound leg again plus handling, and the reverse leg is
# usually less consolidated, so it is charged at a premium.
RETURN_LEG_MULTIPLIER = 1.25
RETURN_HANDLING_COST = 6.50

DEFAULT_GRID_INTENSITY = 0.35  # kg CO2e per kWh, world average-ish

# A supplier that misses its date does not just deliver late. Somebody expedites
# the shipment to protect the line or the customer promise, and expediting means
# air. Not every late delivery is urgent enough to be worth that, so only a share
# of them are assumed to move. This is a planning assumption rather than a
# measured figure, and it is the one number in the supplier stage worth arguing
# about: halve it and the supplier problems roughly halve with it.
EXPEDITE_SHARE_OF_LATE = 0.5


# How much further freight actually travels than the straight line between its
# two ends, by mode. SCREENING ASSUMPTIONS, not constants: the real figure
# depends on the route, and a proper road, rail or sea router would replace
# these (see distance.py, which is the one place that applies them).
#
# Road 1.25 and rail 1.42 follow the median European circuity in Heinold and
# Makowski, "Driving the Extra Mile", Networks and Spatial Economics (2026):
# 1.25 for road (range 1.15 to 1.77) and 1.43 for rail (1.13 to 2.07).
#
# Air 1.05 is a flat allowance for routing and approach. GLEC v3 instead adds
# a fixed 95 km to the airport-to-airport great circle, which is about 1.01 on
# long haul and over 1.2 on a short hop, so this is generous on long routes.
#
# Sea 1.60 is the weakest of the four and no single figure fits. Routed sea
# distance against great circle measured on the routes in both samples ran
# from about 0.9 to 2.8, median about 1.3: Helsinki to Stockholm about 1.07,
# Shanghai to Stockholm about 2.8. So 1.6 overstates short sea crossings and
# understates routes that round a continent or take a canal.
#
# Before 2026-09-13 every mode used 1.0, which understated the slower modes
# most, and those are the ones the tool recommends switching to.
CIRCUITY = {
    "road": 1.25,
    "rail": 1.42,
    "sea": 1.60,
    "air": 1.05,
}


def circuity(mode):
    return CIRCUITY[normalise_mode(mode)]


def emission_factor(mode):
    return EMISSION_FACTORS[normalise_mode(mode)]


def cost_factor(mode):
    return COST_FACTORS[normalise_mode(mode)]


def normalise_mode(mode):
    key = " ".join(str(mode).strip().lower().replace("-", " ").replace("_", " ").split())
    aliases = {
        "truck": "road",
        "trucking": "road",
        "ground": "road",
        "lorry": "road",
        "van": "road",
        "road freight": "road",
        "ftl": "road",
        "ltl": "road",
        "train": "rail",
        "freight rail": "rail",
        "rail freight": "rail",
        "ship": "sea",
        "ocean": "sea",
        "boat": "sea",
        "sea freight": "sea",
        "ocean freight": "sea",
        "plane": "air",
        "airfreight": "air",
        "air freight": "air",
        "air cargo": "air",
    }
    key = aliases.get(key, key)
    if key not in EMISSION_FACTORS:
        raise ValueError(f"unknown transport mode: {mode}")
    return key


# Rough door-to-door speed by mode, in km per day. These turn a mode switch
# into a lead time answer, which is the first question anyone asks about one.
# They are planning figures, not schedules: road is capped by driver hours
# rather than vehicle speed, rail loses hours to terminals and marshalling,
# and a container ship cruising at about 20 knots still spends days in port at
# each end. Right order of magnitude, nothing more.
TRANSIT_KM_PER_DAY = {
    "road": 700,
    "rail": 450,
    "sea": 550,
    "air": 5000,
}

# Time spent at each end whatever the distance: collection, consolidation,
# customs, terminal or port handling, and final delivery. Sea carries the most
# because port dwell and container handling dominate short sea routes.
TRANSIT_FIXED_DAYS = {
    "road": 1.0,
    "rail": 2.0,
    "sea": 9.0,
    "air": 2.0,
}


def transit_km_per_day(mode):
    return TRANSIT_KM_PER_DAY[normalise_mode(mode)]


def transit_fixed_days(mode):
    return TRANSIT_FIXED_DAYS[normalise_mode(mode)]
