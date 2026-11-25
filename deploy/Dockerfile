# asagent API 镜像：多阶段构建（前端 SPA + 单 worker FastAPI）
# 构建参数默认走国内镜像源；海外部署可覆盖为官方源。

# 仅取 Docker CLI 二进制（Debian 的 docker.io 只含 daemon，不含 docker 命令）
FROM docker:27-cli AS dockercli

FROM node:20-bookworm-slim AS web

ARG NPM_REGISTRY=https://registry.npmjs.org

WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
# lock 里写的是 registry.npmjs.org 的 resolved URL，换源时一并改写
RUN if [ "$NPM_REGISTRY" != "https://registry.npmjs.org" ]; then \
      sed -i "s|https://registry.npmjs.org|$NPM_REGISTRY|g" package-lock.json; \
    fi \
    && npm ci --registry="$NPM_REGISTRY"
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

# WITH_RAG=0 可构建不含 torch 的轻量镜像（RAG 场景不可用）
ARG WITH_RAG=1
# WITH_DOCKER_CLI=0 可省略容器内的 docker CLI（code 场景不可用）
ARG WITH_DOCKER_CLI=1
ARG PIP_INDEX_URL=https://pypi.org/simple
# uv.lock 记录的是 files.pythonhosted.org 的下载地址，frozen 安装会按该 URL 拉包；
# 国内网络直连常超时，这里替换成路径结构一致的 PyPI 镜像（置空则用官方源）
ARG PYPI_FILES_MIRROR=https://pypi.tuna.tsinghua.edu.cn
ARG UV_VERSION=0.13.1
ARG APT_MIRROR=
ARG NPM_REGISTRY=https://registry.npmjs.org
# torch CPU wheel 的国内镜像（阿里云与官方目录结构一致）；置空则用官方源
ARG TORCH_WHEEL_MIRROR=https://mirrors.aliyun.com/pytorch-wheels/cpu

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    TZ=Asia/Shanghai \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|$APT_MIRROR|g; s|http://security.debian.org|$APT_MIRROR|g" \
        /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" "uv==$UV_VERSION"

ENV UV_DEFAULT_INDEX=$PIP_INDEX_URL

WORKDIR /app

# 先只装第三方依赖，利用 Docker 层缓存
COPY pyproject.toml uv.lock README.md ./
# 只改镜像内的 lock 副本，仓库里的 uv.lock 仍指向官方源
RUN if [ -n "$TORCH_WHEEL_MIRROR" ]; then \
      sed -i "s|https://download-r2.pytorch.org/whl/cpu|$TORCH_WHEEL_MIRROR|g; s|https://download.pytorch.org/whl/cpu|$TORCH_WHEEL_MIRROR|g" uv.lock; \
    fi \
    && if [ -n "$PYPI_FILES_MIRROR" ]; then \
      sed -i "s|https://files.pythonhosted.org|$PYPI_FILES_MIRROR|g" uv.lock; \
    fi
RUN if [ "$WITH_RAG" = "1" ]; then \
      uv sync --frozen --no-install-project --no-dev --extra rag --extra milvus --extra state; \
    else \
      uv sync --frozen --no-install-project --no-dev --extra state; \
    fi

COPY src/ src/
RUN if [ "$WITH_RAG" = "1" ]; then \
      uv sync --frozen --no-dev --extra rag --extra milvus --extra state; \
    else \
      uv sync --frozen --no-dev --extra state; \
    fi

COPY configs/ configs/
COPY evals/ evals/
COPY scripts/ scripts/
COPY data/docs/ data/docs/
COPY --from=web /app/web/dist web/dist
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
RUN if [ "$WITH_DOCKER_CLI" = "0" ]; then rm -f /usr/local/bin/docker; fi

# 不要 chown -R /app：那会把整个 .venv 复制进新层（磁盘占用翻倍）。
# .venv 与源码只需可读；运行时要写的 runtime/、data/ 在 compose 中挂载。
RUN groupadd -r docker 2>/dev/null || true \
    && useradd --create-home --uid 10001 app \
    && usermod -aG docker app \
    && mkdir -p /app/runtime /app/data \
    && chown app:app /app/runtime /app/data

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["/app/.venv/bin/asagent", "web", "--host", "0.0.0.0", "--port", "8000"]
