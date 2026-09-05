"""RPM authoring backed by an existing TimeBetweenShots value, never a new node."""
from decimal import Decimal, DecimalException, localcontext

from lxml import etree

RPM_KEY = "weapon.roundsPerMinute"
INTERVAL_KEY = "weapon.timeBetweenShots"
MIN_RPM = 1
MAX_RPM = 60000
_PRECISION = Decimal("0.000001")


def _node(item):
    nodes = [child for child in item if isinstance(child.tag, str)
             and etree.QName(child).localname == "TimeBetweenShots"]
    if len(nodes) > 1:
        raise ValueError("Ambiguous TimeBetweenShots node")
    if not nodes or set(nodes[0].attrib) != {"value"} or len(nodes[0]) or (nodes[0].text or "").strip():
        return None
    return nodes[0]


def _formatted(number):
    with localcontext() as context:
        context.prec = 40
        return format(number.quantize(_PRECISION), "f").rstrip("0").rstrip(".") or "0"


def validate_rpm(value: str) -> str:
    try:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError()
        number = Decimal(value)
        if not number.is_finite() or not MIN_RPM <= number <= MAX_RPM:
            raise ValueError()
        return _formatted(number)
    except (ValueError, DecimalException) as exc:
        raise ValueError(f"Fire rate must be a finite number from {MIN_RPM} to {MAX_RPM} RPM") from exc


def interval_for_rpm(value: str) -> str:
    rate = Decimal(validate_rpm(value))
    with localcontext() as context:
        context.prec = 40
        interval = (Decimal(60) / rate).quantize(Decimal("0.000000000000000001"))
    return format(interval, "f").rstrip("0").rstrip(".")


def fire_rate_values(item) -> dict[str, str]:
    node = _node(item)
    if node is None:
        return {}
    raw = node.get("value")
    rpm = ""
    try:
        interval = Decimal(raw)
        if interval.is_finite() and interval > 0:
            with localcontext() as context:
                context.prec = 40
                rpm = _formatted(Decimal(60) / interval)
    except DecimalException:
        pass
    # Invalid source intervals remain visible and repairable, never guessed.
    return {INTERVAL_KEY: raw, RPM_KEY: rpm}


def set_fire_rate(item, value: str) -> tuple[str, str, str, str]:
    values = fire_rate_values(item)
    if RPM_KEY not in values:
        raise ValueError("No existing editable TimeBetweenShots value; schema fields are not synthesized")
    rate = validate_rpm(value)
    interval = interval_for_rpm(rate)
    _node(item).set("value", interval)
    return values[RPM_KEY], rate, values[INTERVAL_KEY], interval
