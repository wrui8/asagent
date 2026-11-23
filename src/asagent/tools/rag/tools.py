from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..base import PermissionLevel, Tool, ToolContext, ToolError, ToolResult
from .index import RagIndex

_MAX_SNIPPET_CHARS = 1200


class _NoArgs(BaseModel):
    pass


class ListDocsTool(Tool[_NoArgs]):
    name = "list_docs"
    description = "列出知识库中的文档（文档 ID、标题、片段数），不确定该查哪篇时先调用。"
    input_model = _NoArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 10.0

    def __init__(self, index: RagIndex):
        self.index = index

    async def execute(self, args: _NoArgs, ctx: ToolContext) -> ToolResult:
        docs = self.index.list_docs()
        lines = [f"- {d.doc_id}：{d.title}（{d.chunks} 个片段）" for d in docs]
        return ToolResult(ok=True, content="知识库文档：\n" + "\n".join(lines))


class SearchDocsArgs(BaseModel):
    query: str = Field(..., min_length=1, description="检索问题或关键词，尽量具体")
    top_k: int = Field(5, ge=1, le=10, description="返回片段数（1-10）")


class SearchDocsTool(Tool[SearchDocsArgs]):
    name = "search_docs"
    description = "在知识库中检索相关片段，返回带引用编号（文档ID#片段序号）的原文。"
    input_model = SearchDocsArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 20.0

    def __init__(self, index: RagIndex, default_top_k: int = 5):
        self.index = index
        self.default_top_k = default_top_k

    async def execute(self, args: SearchDocsArgs, ctx: ToolContext) -> ToolResult:
        hits = self.index.search(args.query, top_k=args.top_k or self.default_top_k)
        if not hits:
            return ToolResult(ok=True, content="没有检索到相关片段。可以换个说法，或调用 list_docs 查看文档清单。")
        lines = [f"检索到 {len(hits)} 个片段（按相关度排序）："]
        for rank, hit in enumerate(hits, 1):
            snippet = hit.text
            if len(snippet) > _MAX_SNIPPET_CHARS:
                snippet = snippet[:_MAX_SNIPPET_CHARS] + "…"
            lines.append(
                f"[{rank}] {hit.citation}《{hit.title}》{(' > ' + hit.section) if hit.section else ''}"
                f"（相关度 {hit.score:.3f}）\n{snippet}"
            )
        return ToolResult(
            ok=True,
            content="\n\n".join(lines),
            data={
                "hits": [
                    {
                        "citation": h.citation,
                        "doc_id": h.doc_id,
                        "title": h.title,
                        "section": h.section,
                        "score": round(h.score, 4),
                        "text": h.text,
                    }
                    for h in hits
                ]
            },
        )


class ReadDocArgs(BaseModel):
    doc_id: str = Field(..., description="文档 ID，必须来自 list_docs 或 search_docs 的结果")
    max_chars: int = Field(6000, ge=500, le=12000, description="最多返回字符数")


class ReadDocTool(Tool[ReadDocArgs]):
    name = "read_doc"
    description = "读取整篇文档原文（可能被截断），用于需要完整上下文的场景。"
    input_model = ReadDocArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 10.0

    def __init__(self, index: RagIndex):
        self.index = index

    async def execute(self, args: ReadDocArgs, ctx: ToolContext) -> ToolResult:
        text = self.index.read_doc(args.doc_id, max_chars=args.max_chars)
        if text is None:
            return ToolResult(
                ok=False,
                content=f"文档不存在：{args.doc_id}。请先调用 list_docs 查看可用文档。",
                error=ToolError(code="doc_not_found", message="doc not found"),
            )
        return ToolResult(ok=True, content=text, data={"doc_id": args.doc_id})


def build_rag_tools(
    *,
    docs_dir: Path,
    index_db: Path,
    embedding_model: str,
    top_k: int = 5,
) -> list[Tool]:
    index = RagIndex(index_db=index_db, docs_dir=docs_dir, embedding_model=embedding_model)
    index.ensure()
    return [
        ListDocsTool(index),
        SearchDocsTool(index, default_top_k=top_k),
        ReadDocTool(index),
    ]
