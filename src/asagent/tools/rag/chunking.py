from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = "。！？；\n"


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    index: int
    title: str
    section: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.doc_id}#{self.index}"

    @property
    def embed_text(self) -> str:
        """检索时使用的文本：文档标题 + 小节标题 + 正文。"""
        parts = [p for p in (self.title, self.section) if p]
        return "\n".join(parts + [self.text])


def _split_long(text: str, max_chars: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            cut = max(text.rfind(ch, start, end) for ch in _SENTENCE_BREAK)
            if cut > start + max_chars // 2:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def _pack_paragraphs(paragraphs: list[str], max_chars: int) -> list[str]:
    packed: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            packed.append("\n\n".join(buf))
            buf.clear()

    for para in paragraphs:
        if len(para) > max_chars:
            flush()
            packed.extend(_split_long(para, max_chars, max_chars // 6))
            continue
        if buf and len("\n\n".join(buf)) + len(para) + 2 > max_chars:
            flush()
        buf.append(para)
    flush()
    return packed


def split_markdown(
    doc_id: str, raw: str, *, max_chars: int = 500, overlap: int = 80
) -> list[Chunk]:
    """按 Markdown 标题与段落切分，保留标题上下文。

    HTML 注释故意保留：真实文档里常藏着不可见的「提示」，
    这类内容必须进入索引，才能测出模型是否会被诱导。
    """
    title = doc_id
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in raw.splitlines():
        match = _HEADING.match(line)
        if match:
            level, heading = len(match.group(1)), match.group(2).strip()
            if level == 1:
                title = heading
                continue
            if level <= 3:
                sections.append((heading, []))
                continue
        sections[-1][1].append(line)

    chunks: list[Chunk] = []
    for section, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        paragraphs = [p.strip() for p in _PARA_SPLIT.split(body) if p.strip()]
        for text in _pack_paragraphs(paragraphs, max_chars):
            chunks.append(
                Chunk(
                    doc_id=doc_id,
                    index=len(chunks),
                    title=title,
                    section=section,
                    text=text,
                )
            )
    return chunks
