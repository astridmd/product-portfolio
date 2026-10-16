"""Money rounding. Cents, ROUND_HALF_UP, applied only where figures become postings."""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def cents(value):
    """Round a float or Decimal to two decimals and return a Decimal."""
    return Decimal(repr(float(value))).quantize(CENT, rounding=ROUND_HALF_UP) \
        if not isinstance(value, Decimal) else value.quantize(CENT, rounding=ROUND_HALF_UP)
