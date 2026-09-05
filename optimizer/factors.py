"""Cost and emission factors per transport mode.

Emission figures are kg CO2e per tonne-km, in line with the ranges published
by DEFRA and the GLEC framework. Cost figures are USD per tonne-km and are
far more variable in reality, so they are treated as defaults a user can
override per lane rather than as ground truth.
"""

MODES = ("road", "rail", "sea", "air")

EMISSION_FACTORS = {
    "road": 0.062,
    "rail": 0.022,
    "sea": 0.008,
    "air": 0.602,
}

# Air is the one worth stating a source for, because getting it wrong throws
# every result off. Long haul general air cargo runs roughly 2 to 5 USD per kg,
# so about 3,000 USD a tonne over a 16,000 km route, which is 0.19 per
# tonne-km. Air is still roughly 20 times road and 30 times sea, which is
# what makes it worth moving off, but it is not 100 times.
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


def emission_factor(mode):
    return EMISSION_FACTORS[normalise_mode(mode)]


def cost_factor(mode):
    return COST_FACTORS[normalise_mode(mode)]


def normalise_mode(mode):
    key = str(mode).strip().lower()
    aliases = {
        "truck": "road",
        "ground": "road",
        "lorry": "road",
        "van": "road",
        "train": "rail",
        "freight rail": "rail",
        "ship": "sea",
        "ocean": "sea",
        "boat": "sea",
        "plane": "air",
        "airfreight": "air",
        "air freight": "air",
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
