"""Money, date and id helpers."""
import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .errors import ValidationError

CENT = Decimal("0.01")


def D(value) -> Decimal:
    """Convert to Decimal (via str, so floats do not leak binary noise)."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception as exc:  # decimal.InvalidOperation
        raise ValidationError(f"invalid number: {value!r}") from exc


def money(value) -> Decimal:
    """Round to 2 dp, half-up. All currency amounts pass through here."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def add_months(d: date, months: int) -> date:
    """Add calendar months, clamping the day (31 Jan + 1m = 28/29 Feb)."""
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    month += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def expiry_for(effective: date, months: int) -> date:
    """Last day of cover: effective + months - 1 day."""
    return add_months(effective, months) - timedelta(days=1)


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-]{6,18}[0-9]$")


class IdGenerator:
    """Sequential ids like CUS-000001 (per prefix)."""

    def __init__(self):
        self._counters = {}

    def next(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{n:06d}"
