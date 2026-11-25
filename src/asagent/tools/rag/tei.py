from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class TEIError(RuntimeError):
    """TEI 服务不可用或返回异常结果。"""


class TEIClient:
    """HuggingFace Text Embeddings Inference 的最小客户端。

    只依赖 `/embed` 与 `/health` 两个端点；TEI 在 CPU 上运行 bge-m3，
    输出 1024 维稠密向量（`--pooling cls` + `normalize`）。
    """

    def __init__(
        self,
        base_url: str,
        *,
        batch_size: int = 8,
        timeout: float = 120.0,
        normalize: bool = True,
        transport: httpx.BaseTransport | None = None,
    ):
        base = base_url.strip().rstrip("/")
        if not base:
            raise ValueError("TEI 地址为空：请设置 AS_TEI_URL")
        self.base_url = base
        self.batch_size = max(1, batch_size)
        self.timeout = timeout
        self.normalize = normalize
        self._transport = transport
        self._http: httpx.Client | None = None

    # ------------------------------------------------------------------ #
    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                transport=self._transport,
            )
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def health(self) -> bool:
        try:
            resp = self._client().get("/health", timeout=5.0)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    def embed(self, texts: list[str]) -> list[list[float]]:
        """按批编码；返回顺序与入参一致。空文本以单个空格占位（TEI 拒绝空串）。"""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [t if t.strip() else " " for t in texts[start : start + self.batch_size]]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, batch: list[str], *, retry: bool = True) -> list[list[float]]:
        payload = {"inputs": batch, "truncate": True, "normalize": self.normalize}
        try:
            resp = self._client().post("/embed", json=payload)
        except httpx.HTTPError as exc:
            # 长期运行后连接池里的连接可能已被服务端关闭，重建一次再试
            self.close()
            if retry:
                return self._embed_batch(batch, retry=False)
            raise TEIError(f"TEI 请求失败: {exc}") from exc
        if resp.status_code != 200:
            raise TEIError(f"TEI 返回 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if isinstance(data, list) and data and all(
            isinstance(x, (int, float)) for x in data
        ):
            data = [data]  # 单条输入被展开成一维
        if not isinstance(data, list) or len(data) != len(batch):
            raise TEIError(f"TEI 返回条数不符：期望 {len(batch)}，实际 {type(data).__name__}")
        return [[float(x) for x in vec] for vec in data]
