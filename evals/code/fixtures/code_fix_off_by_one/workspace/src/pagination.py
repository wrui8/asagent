def page_slice(items: list, page: int, page_size: int) -> list:
    """返回第 page 页（从 1 开始）的元素；超出范围返回空列表。"""
    if page < 1 or page_size < 1:
        return []
    start = (page - 1) * page_size
    end = start + page_size - 1
    return items[start:end]
