"""Shared, side-effect-free validation for partial-charge scaling."""

from decimal import Decimal
import math


DEFAULT_ION_CHARGE_SCALE = 1.0


def validate_ion_charge_scale(value: object = 1.0) -> float:
    """Accept finite JSON numbers in [0.60, 1.00] on the hundredth grid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("ion_charge_scale 必须是数值，不接受 bool、字符串或其他类型")
    if not 0.60 <= value <= 1.00 or not math.isfinite(value):
        raise ValueError("ion_charge_scale 必须是 0.60..1.00（含边界）内的有限数值，不允许截断越界")
    numerator, denominator = Decimal(str(value)).as_integer_ratio()
    if numerator * 100 % denominator:
        raise ValueError("ion_charge_scale 仅允许百分之一精度，不能超过两位有效小数")
    return float(value)
