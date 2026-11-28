"""Bounded decimal arithmetic and common unit conversions."""

from decimal import Decimal, InvalidOperation, localcontext

UNITS = {
    "mm": ("length", "0.001"), "cm": ("length", "0.01"),
    "m": ("length", "1"), "km": ("length", "1000"),
    "in": ("length", "0.0254"), "ft": ("length", "0.3048"),
    "mi": ("length", "1609.344"), "g": ("mass", "0.001"),
    "kg": ("mass", "1"), "lb": ("mass", "0.45359237"),
    "s": ("time", "1"), "min": ("time", "60"), "h": ("time", "3600"),
}


def calculate(operation, values, from_unit="", to_unit=""):
    if not 1 <= len(values) <= 200:
        raise ValueError("Provide between 1 and 200 numbers.")
    numbers = []
    for value in values:
        if len(value) > 80:
            raise ValueError("Each number must be at most 80 characters.")
        try:
            number = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("Each value must be a decimal number.") from error
        if not number.is_finite() or not -100 <= number.adjusted() <= 100:
            raise ValueError("Numbers must be finite and within the supported magnitude.")
        numbers.append(number)
    unit = ""
    with localcontext() as context:
        context.prec = 28
        if operation == "sum":
            result = sum(numbers)
        elif operation == "average":
            result = sum(numbers) / len(numbers)
        elif operation in {"percentage", "divide"}:
            if len(numbers) != 2 or not numbers[1]:
                raise ValueError("Provide two numbers with a nonzero denominator.")
            result = numbers[0] / numbers[1]
            if operation == "percentage":
                result *= 100
                unit = "%"
        elif operation == "convert":
            source = UNITS.get(from_unit.strip().lower())
            unit = to_unit.strip().lower()
            target = UNITS.get(unit)
            if len(numbers) != 1 or not source or not target or source[0] != target[0]:
                raise ValueError("Provide one value and two supported units of the same dimension.")
            result = numbers[0] * Decimal(source[1]) / Decimal(target[1])
        else:
            raise ValueError("Unsupported operation.")
        return {"value": format(Decimal(result).normalize(), "f"), "unit": unit, "precision": context.prec}


def summarize_rows(rows):
    """Sum amounts by group in first-seen order, skipping blank amounts."""
    if not 1 <= len(rows) <= 200:
        raise ValueError("Provide between 1 and 200 rows.")
    groups = {}
    for row in rows:
        group, amount = row.get("group"), row.get("amount")
        if not isinstance(group, str) or not group.strip() or len(group) > 100:
            raise ValueError("Each row needs a group name of 1 to 100 characters.")
        if amount is None or amount == "":
            continue
        if not isinstance(amount, str):
            raise ValueError("Each amount must be a decimal string or blank.")
        groups.setdefault(group, []).append(amount)
    return [{"group": group, "total": calculate("sum", values)["value"]}
            for group, values in groups.items()]
