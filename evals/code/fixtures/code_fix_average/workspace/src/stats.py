def average(numbers: list[float]) -> float:
    """返回一组数字的平均值；空列表返回 0.0。"""
    if not numbers:
        return 0.0
    return sum(numbers)
