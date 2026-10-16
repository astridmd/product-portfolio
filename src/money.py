"""Decimal helpers. Amounts are never floats."""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
EIGHT = Decimal("0.00000001")
ZERO = Decimal("0")


def D(x):
    """Decimal from a string, int or Decimal. Thousands separators are not accepted."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x).strip())


def q2(x):
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def q8(x):
    return D(x).quantize(EIGHT, rounding=ROUND_HALF_UP)


def fmt(x, places=2):
    return "{:,.{p}f}".format(D(x), p=places)
