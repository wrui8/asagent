"""v2 存储分层：SQLite（v1）与 PostgreSQL（阶段 2）共用同一组接口。"""

from .factory import Storage, build_storage

__all__ = ["Storage", "build_storage"]
