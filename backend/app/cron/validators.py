_FIELD_RANGES = [
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),   # day of week (0 and 7 both = Sunday)
]


def _validate_field(value: str, min_val: int, max_val: int) -> bool:
    """Validate a single cron field. Supports: *, */N, N-M, N,M,O, plain int."""
    if value == "*":
        return True
    if value.startswith("*/"):
        n = value[2:]
        return n.isdigit() and min_val <= int(n) <= max_val
    if "," in value:
        return all(_validate_field(v, min_val, max_val) for v in value.split(","))
    if "-" in value:
        parts = value.split("-", 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            a, b = int(parts[0]), int(parts[1])
            return min_val <= a <= max_val and min_val <= b <= max_val and a <= b
        return False
    return value.isdigit() and min_val <= int(value) <= max_val


def validate_cron_expression(expr: str) -> tuple[str, str, str, str, str]:
    """
    Validate a 5-field cron expression and return (minute, hour, dom, month, dow).
    Raises ValueError if invalid.
    """
    if expr.startswith("@"):
        raise ValueError(
            f"Special schedules like '{expr}' are not supported. "
            "Use standard 5-field cron format."
        )
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(
            f"Cron expression must have exactly 5 fields, got {len(fields)}: '{expr}'"
        )
    field_names = ["minute", "hour", "day-of-month", "month", "day-of-week"]
    for i, (field, (min_v, max_v)) in enumerate(zip(fields, _FIELD_RANGES)):
        if not _validate_field(field, min_v, max_v):
            raise ValueError(
                f"Invalid {field_names[i]} field '{field}' "
                f"in cron expression '{expr}'"
            )
    minute, hour, dom, month, dow = fields
    return minute, hour, dom, month, dow
