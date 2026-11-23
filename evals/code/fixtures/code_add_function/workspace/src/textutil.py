import re  # noqa: F401 - 实现 slugify 时可能会用到


def slugify(text: str) -> str:
    """把标题转成 URL 友好的 slug。

    规则：全部转小写；连续的非字母数字字符替换为单个 '-'；去掉首尾的 '-'。
    例如 "Hello, World!" -> "hello-world"。
    """
    raise NotImplementedError("slugify 尚未实现")
