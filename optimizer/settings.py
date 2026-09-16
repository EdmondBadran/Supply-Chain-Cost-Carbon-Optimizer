"""The optional inputs, and the published defaults they start from.

Nothing in here has to be filled in. Every value has a default that is either
the published factor or "not set", and a workspace that never opens the
Improve accuracy page behaves exactly as it did before this module existed.
That is the whole design rule: an advanced input a person ignores must cost
them nothing.

Three things can be told to the tool, and only three, because these are the
ones that change which change comes top:

  rates            what freight actually costs this company per tonne-km,
                   instead of the published default
  max_extra_days   how much slower a delivery is allowed to get before a
                   change is not worth proposing
  capital_rate     what money tied up in stock costs, which is what makes a
                   slower mode cost something the freight bill does not show

Settings survive an upload. They describe the business, not the file, in the
same way the chain stages do.
"""

from . import factors

TABLE = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# No limit and no carrying cost, so an untouched workspace blocks nothing and
# deducts nothing. A rate of None means "use the published factor".
DEFAULTS = {
    "max_extra_days": None,
    "capital_rate": None,
}

# What a person is allowed to enter, so a typo cannot quietly become an
# answer. A rate outside this is far more likely to be a unit mistake, cents
# for dollars or per kg for per tonne-km, than a real contract.
RATE_RANGE = (0.0001, 10.0)
MAX_EXTRA_DAYS_RANGE = (0, 365)
CAPITAL_RATE_RANGE = (0.0, 1.0)


class SettingError(ValueError):
    pass


def init(conn):
    """db.init makes this table too. This is for a connection that was built
    before the table existed."""
    conn.executescript(TABLE)
    conn.commit()


def _read(conn):
    init(conn)
    return {
        row["key"]: row["value"]
        for row in conn.execute("SELECT key, value FROM settings")
    }


def _write(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )


def rates(conn):
    """The cost per tonne-km to use for each mode, published where the
    company has not said otherwise. Always complete, so callers never have to
    check whether a mode was overridden."""
    stored = _read(conn)
    return {
        mode: float(stored.get(f"rate_{mode}", factors.COST_FACTORS[mode]))
        for mode in factors.MODES
    }


def overridden(conn):
    """Which modes carry a rate of the company's own."""
    stored = _read(conn)
    return {mode for mode in factors.MODES if f"rate_{mode}" in stored}


def current(conn):
    """Every setting, with what each one is and whether it was set. This is
    what the page renders, so the form and the engine cannot disagree."""
    stored = _read(conn)
    return {
        "rates": rates(conn),
        "overridden": overridden(conn),
        "published": dict(factors.COST_FACTORS),
        "max_extra_days": _number(stored.get("max_extra_days")),
        "capital_rate": _number(stored.get("capital_rate")),
        "any_set": bool(stored),
    }


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summary(conn):
    """One line saying what has been told to the tool, for the results page
    to show without opening anything."""
    now = current(conn)
    parts = []
    if now["overridden"]:
        modes = ", ".join(sorted(now["overridden"]))
        parts.append(f"your own {modes} rates")
    if now["max_extra_days"] is not None:
        days = int(now["max_extra_days"])
        parts.append(f"a limit of {days} extra day{'s' if days != 1 else ''}")
    if now["capital_rate"]:
        parts.append(f"stock at {now['capital_rate']:.0%} a year")
    if not parts:
        return None
    if len(parts) == 1:
        return f"Using {parts[0]}."
    return f"Using {', '.join(parts[:-1])} and {parts[-1]}."


def save(conn, form):
    """Store what was entered, refusing anything that cannot be a rate.

    A blank field clears that setting rather than storing a zero, so leaving
    the page empty puts the published defaults back.
    """
    init(conn)
    changes = 0

    for mode in factors.MODES:
        raw = (form.get(f"rate_{mode}") or "").strip()
        if not raw:
            _write(conn, f"rate_{mode}", None)
            continue
        value = _parse(raw, f"the {mode} rate")
        low, high = RATE_RANGE
        if not low <= value <= high:
            raise SettingError(
                f"The {mode} rate of {raw} is outside {low} to {high} USD per "
                "tonne-km. Check whether it is per kg rather than per tonne."
            )
        _write(conn, f"rate_{mode}", value)
        changes += 1

    raw = (form.get("max_extra_days") or "").strip()
    if raw:
        value = _parse(raw, "the extra days limit")
        low, high = MAX_EXTRA_DAYS_RANGE
        if not low <= value <= high:
            raise SettingError(f"The extra days limit has to be between {low} and {high}.")
        _write(conn, "max_extra_days", int(value))
        changes += 1
    else:
        _write(conn, "max_extra_days", None)

    raw = (form.get("capital_rate") or "").strip()
    if raw:
        value = _parse(raw, "the cost of capital")
        # People write 8 as often as 0.08, and both mean the same thing.
        if value > 1.0:
            value = value / 100.0
        low, high = CAPITAL_RATE_RANGE
        if not low <= value <= high:
            raise SettingError("The cost of capital has to be between 0 and 100 percent.")
        _write(conn, "capital_rate", value)
        changes += 1
    else:
        _write(conn, "capital_rate", None)

    conn.commit()
    return changes


def clear(conn):
    init(conn)
    conn.execute("DELETE FROM settings")
    conn.commit()


def _parse(raw, what):
    try:
        return float(raw.replace(",", "").replace("%", "").strip())
    except ValueError:
        raise SettingError(f"{what.capitalize()} is not a number: {raw}") from None
