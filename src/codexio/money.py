"""Two-decimal dollar display, independent of stored pricing precision."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


def usd(value, *, compact=False, symbol=True, missing="未定价"):
    if value is None or isinstance(value, bool):
        return missing
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return missing
    if not number.is_finite() or number < 0:
        return missing
    suffix = ""
    if compact:
        for exponent, unit in ((18, "E"), (15, "P"), (12, "T"), (9, "B"), (6, "M"), (3, "K")):
            if number >= 10 ** exponent:
                number /= 10 ** exponent
                suffix = unit
                break
    with localcontext() as context:
        context.prec = max(28, number.adjusted() + 4)
        rounded = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return ("$" if symbol else "") + format(abs(rounded), ",.2f" if symbol else ".2f") + suffix
