def apply_discount(price: float, percent: float) -> float:
    """按百分比折扣返回折后价格，例如 apply_discount(200, 25) -> 150.0。"""
    return price - percent
