import {
  AlertTriangle,
  BookOpen,
  Clock,
  FilePlus2,
  History,
  Pencil,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  createKBDoc,
  deleteKBDoc,
  fetchKBAudit,
  fetchKBDoc,
  fetchKBDocs,
  fetchKBJobs,
  fetchKBStatus,
  fetchKBVersions,
  reindexKB,
  restoreKBDoc,
  rollbackKBDoc,
  updateKBDoc,
} from "../lib/api";
import { errorMessage } from "../lib/errors";
import {
  fmtBytes,
  fmtDate,
  fmtDateTime,
  fmtInt,
  fmtRelative,
  fmtTime,
  shortId,
} from "../lib/format";
import type { KBDoc, KBDocDetail, KBDocVersion } from "../lib/types";
import { useUI } from "../stores/ui";
import { Empty } from "../components/Empty";
import { KpiCard } from "../components/KpiCard";
import { Modal } from "../components/Modal";
import { Tilt } from "../components/Tilt";

type EditorState =
  | { mode: "create"; docId?: string; title?: string; content?: string }
  | { mode: "edit"; docId: string }
  | null;

const DOC_ID_RE = /^[a-z0-9_-]{2,64}$/;

export function KB() {
  const queryClient = useQueryClient();
  const toast = useUI((s) => s.toast);
  const [searchParams, setSearchParams] = useSearchParams();

  const [search, setSearch] = useState("");
  const [showDeleted, setShowDeleted] = useState(true);
  const [editor, setEditor] = useState<EditorState>(null);
  const [versionsFor, setVersionsFor] = useState<string | null>(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const autoReindex = useRef(false);

  const statusQuery = useQuery({
    queryKey: ["kb", "status"],
    queryFn: fetchKBStatus,
    refetchInterval: (query) => (query.state.data?.indexing ? 2000 : 8000),
  });
  const docsQuery = useQuery({
    queryKey: ["kb", "docs"],
    queryFn: () => fetchKBDocs(true),
    refetchInterval: 8000,
  });
  const jobsQuery = useQuery({
    queryKey: ["kb", "jobs"],
    queryFn: fetchKBJobs,
    refetchInterval: statusQuery.data?.indexing ? 2000 : false,
  });
  const auditQuery = useQuery({
    queryKey: ["kb", "audit"],
    queryFn: fetchKBAudit,
  });

  const status = statusQuery.data;
  const docs = docsQuery.data ?? [];

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["kb"] });
  };

  const reindex = useMutation({
    mutationFn: (docIds?: string[]) => reindexKB(docIds),
    onSuccess: (_data, docIds) => {
      toast(docIds?.length ? "已提交单篇重建" : "全量重建已提交", "ok");
      invalidateAll();
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  useEffect(() => {
    if (autoReindex.current) return;
    if (searchParams.get("reindex") !== "1") return;
    autoReindex.current = true;
    reindex.mutate(undefined);
    setSearchParams({}, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const remove = useMutation({
    mutationFn: (docId: string) => deleteKBDoc(docId),
    onSuccess: () => {
      toast("已移入回收站，并从向量索引中移除", "warn");
      invalidateAll();
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  const restore = useMutation({
    mutationFn: (docId: string) => restoreKBDoc(docId),
    onSuccess: () => {
      toast("已从回收站恢复，请重建索引以恢复检索", "ok");
      invalidateAll();
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return docs
      .filter((doc) => (showDeleted ? true : doc.status === "active"))
      .filter((doc) => {
        if (!q) return true;
        return (
          doc.title.toLowerCase().includes(q) ||
          doc.doc_id.toLowerCase().includes(q)
        );
      })
      .sort((a, b) => {
        if (a.status !== b.status) return a.status === "active" ? -1 : 1;
        return a.updated_at < b.updated_at ? 1 : -1;
      });
  }, [docs, search, showDeleted]);

  const onPickFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      toast("单文件不能超过 2MB", "err");
      return;
    }
    if (!/\.(md|txt)$/i.test(file.name)) {
      toast("仅支持 .md / .txt 文档", "err");
      return;
    }
    setUploadBusy(true);
    try {
      const content = await file.text();
      const base = file.name.replace(/\.(md|txt)$/i, "");
      const docId =
        base
          .toLowerCase()
          .replace(/[^a-z0-9_-]+/g, "-")
          .replace(/^-+|-+$/g, "")
          .slice(0, 64) || `doc-${Date.now().toString(36)}`;
      setEditor({
        mode: "create",
        docId,
        title: base,
        content,
      });
    } finally {
      setUploadBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const progressWidth = status?.indexing ? 100 : status?.stale ? 42 : 100;
  const progressText = status?.indexing
    ? "正在重建索引…"
    : status?.stale
      ? "文档有改动，等待重建"
      : "索引与磁盘内容一致";

  return (
    <section className="page">
      <div className="page-head stagger">
        <div>
          <div className="eyebrow">knowledge base</div>
          <h2>
            知识<span className="grad-text">库</span>
          </h2>
          <p>文档保存即可编辑；向量索引需重建后才会对 RAG 检索生效。知识库对所有登录用户共享。</p>
        </div>
        <div className="spacer" />
        <input
          ref={fileRef}
          type="file"
          accept=".md,.txt"
          style={{ display: "none" }}
          onChange={(event) => void onPickFile(event.target.files?.[0])}
        />
        <button
          className="btn"
          disabled={uploadBusy}
          onClick={() => fileRef.current?.click()}
        >
          <Upload size={15} />
          {uploadBusy ? "读取中…" : "上传文档"}
        </button>
        <button className="btn" onClick={() => setEditor({ mode: "create" })}>
          <FilePlus2 size={15} />
          新建文档
        </button>
        <button
          className="btn btn-primary"
          disabled={reindex.isPending || status?.indexing}
          onClick={() => reindex.mutate(undefined)}
        >
          <RefreshCw size={15} className={status?.indexing ? "spin" : ""} />
          重建索引
        </button>
      </div>

      <div className="kpi-row stagger">
        <Tilt>
          <KpiCard
            label="文档"
            value={status?.docs_total ?? 0}
            icon={<BookOpen size={13} />}
            sub={`回收站 ${status?.docs_deleted ?? 0} 篇`}
          />
        </Tilt>
        <Tilt>
          <KpiCard
            label="索引片段"
            value={status?.chunks ?? 0}
            icon={<Search size={13} />}
            sub={`索引文档 ${status?.index_docs ?? 0} 篇`}
          />
        </Tilt>
        <Tilt>
          <div className="card kpi lift hud">
            <div className="halo" />
            <div className="label">
              <AlertTriangle size={13} />
              索引状态
            </div>
            <div
              className="value"
              style={{
                fontSize: 19,
                color: status?.stale ? "var(--warn)" : "var(--ok)",
              }}
            >
              {status ? (status.stale ? "已过期" : "已同步") : "—"}
            </div>
            <div className="sub">
              {status?.suspicious_docs
                ? `${status.suspicious_docs} 篇含可疑指令`
                : progressText}
            </div>
          </div>
        </Tilt>
        <Tilt>
          <div className="card kpi lift hud">
            <div className="halo" />
            <div className="label">
              <Clock size={13} />
              上次重建
            </div>
            <div className="value" style={{ fontSize: 19 }}>
              {status?.built_at ? fmtRelative(status.built_at) : "从未"}
            </div>
            <div className="sub mono">指纹 {status?.fingerprint || "—"}</div>
          </div>
        </Tilt>
      </div>

      <div className="split stagger">
        <div className="card table-wrap hud">
          <div className="toolbar" style={{ padding: "14px 15px 0", marginBottom: 10 }}>
            <div className="search" style={{ width: 240 }}>
              <Search size={14} />
              <input
                placeholder="按标题或文档 ID 搜索"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={showDeleted}
                onChange={(event) => setShowDeleted(event.target.checked)}
              />
              显示回收站
            </label>
          </div>
          {docsQuery.isLoading ? (
            <div style={{ padding: 16 }}>
              {[0, 1, 2, 3].map((i) => (
                <div key={i} className="skeleton" style={{ marginBottom: 12 }} />
              ))}
            </div>
          ) : docsQuery.isError ? (
            <Empty title="加载失败">{errorMessage(docsQuery.error)}</Empty>
          ) : filtered.length === 0 ? (
            <Empty icon={<BookOpen size={20} />} title="还没有知识文档">
              新建或上传 .md / .txt 文档，然后重建索引，RAG 会话即可检索。
            </Empty>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>文档</th>
                  <th className="hide-sm" style={{ textAlign: "right" }}>片段</th>
                  <th className="hide-sm">最后编辑</th>
                  <th className="hide-sm">更新时间</th>
                  <th>索引</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {filtered.map((doc) => (
                  <DocRow
                    key={doc.doc_id}
                    doc={doc}
                    onEdit={() => setEditor({ mode: "edit", docId: doc.doc_id })}
                    onHistory={() => setVersionsFor(doc.doc_id)}
                    onDelete={() => {
                      if (window.confirm(`将「${doc.title}」移入回收站？索引会立即移除。`)) {
                        remove.mutate(doc.doc_id);
                      }
                    }}
                    onRestore={() => restore.mutate(doc.doc_id)}
                    onReindex={() => reindex.mutate([doc.doc_id])}
                    reindexing={status?.indexing ?? false}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="stack">
          <div className="card card-pad hud">
            <div
              className="row"
              style={{ justifyContent: "space-between", marginBottom: 12 }}
            >
              <div className="label-sm" style={{ margin: 0 }}>
                索引与重建
              </div>
              <span className="badge badge-mute" style={{ fontSize: 9 }}>
                单飞锁
              </span>
            </div>
            <div className="quota-bar" style={{ height: 7 }}>
              <i
                style={{
                  width: `${progressWidth}%`,
                  opacity: status?.stale && !status.indexing ? 0.55 : 1,
                }}
              />
            </div>
            <div
              className="row"
              style={{
                justifyContent: "space-between",
                margin: "8px 0 14px",
                fontSize: 11,
                color: "var(--faint)",
              }}
            >
              <span>{progressText}</span>
              <span>{status?.indexing ? "进行中" : status?.stale ? "待重建" : "100%"}</span>
            </div>
            <div className="stack" style={{ gap: 9, fontSize: 12 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span style={{ color: "var(--muted)" }}>embedding</span>
                <span className="mono" style={{ fontSize: 11 }}>
                  bge-base-zh
                </span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span style={{ color: "var(--muted)" }}>fingerprint</span>
                <span className="mono" style={{ fontSize: 11 }}>
                  {status?.fingerprint || "—"}
                </span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span style={{ color: "var(--muted)" }}>片段 / 文档</span>
                <span className="mono">
                  {fmtInt(status?.chunks)} / {fmtInt(status?.docs_total)}
                </span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span style={{ color: "var(--muted)" }}>重建冷却</span>
                <span className="mono">
                  {status?.cooldown_s ? `${status.cooldown_s}s` : "就绪"}
                </span>
              </div>
            </div>
            <button
              className="btn btn-primary btn-block"
              style={{ marginTop: 15 }}
              disabled={reindex.isPending || status?.indexing}
              onClick={() => reindex.mutate(undefined)}
            >
              {status?.indexing ? "正在重建…" : "重建全部索引"}
            </button>
            <p className="hint" style={{ marginTop: 8 }}>
              重建在后台异步执行；单篇文档改动可使用行内「重建」做增量索引。
            </p>
          </div>

          <div className="card card-pad hud">
            <div className="label-sm" style={{ marginBottom: 9 }}>
              重建日志
            </div>
            <div className="codebox" style={{ maxHeight: 180 }}>
              {(jobsQuery.data ?? []).length === 0
                ? "暂无重建记录"
                : (jobsQuery.data ?? [])
                    .slice(0, 8)
                    .map((job) => {
                      const label =
                        job.status === "done"
                          ? "完成"
                          : job.status === "failed"
                            ? "失败"
                            : "运行中";
                      const scope = job.doc_ids ? "增量" : "全量";
                      return `${fmtTime(job.started_at)}  ${scope}${label}${
                        job.message ? ` · ${job.message}` : ""
                      }`;
                    })
                    .join("\n")}
            </div>
          </div>

          <div className="card card-pad hud">
            <div className="label-sm" style={{ marginBottom: 9 }}>
              最近写操作
            </div>
            <div className="stack" style={{ gap: 7 }}>
              {(auditQuery.data ?? []).slice(0, 5).map((item) => (
                <div
                  key={item.id}
                  className="row"
                  style={{ justifyContent: "space-between", fontSize: 11.5 }}
                >
                  <span className="mono" style={{ color: "var(--faint)" }}>
                    {fmtTime(item.created_at)}
                  </span>
                  <span style={{ color: "var(--muted)" }}>
                    {item.actor ?? "system"} · {item.action}
                  </span>
                  <span className="tag">{item.doc_id ?? "—"}</span>
                </div>
              ))}
              {(auditQuery.data ?? []).length === 0 ? (
                <span style={{ fontSize: 12, color: "var(--faint)" }}>
                  暂无写操作记录
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      <p className="footnote">
        删除进入回收站 · 每次写入保留最近 20 个版本 · 含可疑指令的文档会标记但不会自动下架 · 索引层需要重建才生效
      </p>

      {editor ? (
        <KBEditor
          state={editor}
          onClose={() => setEditor(null)}
          onSaved={() => {
            setEditor(null);
            invalidateAll();
          }}
        />
      ) : null}

      {versionsFor ? (
        <VersionsModal
          docId={versionsFor}
          onClose={() => setVersionsFor(null)}
          onRolled={() => {
            setVersionsFor(null);
            invalidateAll();
          }}
        />
      ) : null}
    </section>
  );
}

function DocRow({
  doc,
  onEdit,
  onHistory,
  onDelete,
  onRestore,
  onReindex,
  reindexing,
}: {
  doc: KBDoc;
  onEdit: () => void;
  onHistory: () => void;
  onDelete: () => void;
  onRestore: () => void;
  onReindex: () => void;
  reindexing: boolean;
}) {
  const deleted = doc.status === "deleted";
  return (
    <tr>
      <td>
        <div className="cell-title">
          {doc.title}
          {doc.suspicious ? (
            <span className="badge badge-fail" style={{ marginLeft: 8, fontSize: 9 }}>
              含可疑指令
            </span>
          ) : null}
        </div>
        <div className="cell-sub">
          {doc.doc_id}.md · {fmtBytes(doc.size)}
        </div>
      </td>
      <td className="num hide-sm">{fmtInt(doc.chunks)}</td>
      <td className="hide-sm">{doc.updated_by ?? "—"}</td>
      <td className="hide-sm">
        <div style={{ fontWeight: 600 }}>{fmtTime(doc.updated_at)}</div>
        <div className="cell-sub">{fmtDate(doc.updated_at)}</div>
      </td>
      <td>
        {deleted ? (
          <span className="badge badge-fail">
            <span className="dot" />
            已删除
          </span>
        ) : doc.indexed ? (
          <span className="badge badge-ok">
            <span className="dot" />
            已索引
          </span>
        ) : (
          <span className="badge badge-wait">
            <span className="dot" />
            待重建
          </span>
        )}
      </td>
      <td>
        <div className="table-actions">
          {deleted ? (
            <button className="btn btn-sm btn-ghost" onClick={onRestore}>
              <RotateCcw size={13} />
              恢复
            </button>
          ) : (
            <>
              <button className="btn btn-sm btn-ghost" onClick={onEdit}>
                <Pencil size={13} />
                编辑
              </button>
              <button className="btn btn-sm btn-ghost" onClick={onHistory}>
                <History size={13} />
                历史
              </button>
              {doc.indexed ? null : (
                <button
                  className="btn btn-sm btn-ghost"
                  disabled={reindexing}
                  onClick={onReindex}
                  title="对单篇文档做增量索引"
                >
                  重建
                </button>
              )}
              <button className="btn btn-sm btn-ghost" onClick={onDelete} title="移入回收站">
                <Trash2 size={13} />
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

function KBEditor({
  state,
  onClose,
  onSaved,
}: {
  state: Exclude<EditorState, null>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useUI((s) => s.toast);
  const editing = state.mode === "edit";
  const [docId, setDocId] = useState(
    state.mode === "create" ? (state.docId ?? "") : state.docId,
  );
  const [title, setTitle] = useState(state.mode === "create" ? (state.title ?? "") : "");
  const [content, setContent] = useState(
    state.mode === "create" ? (state.content ?? "") : "",
  );
  const [error, setError] = useState("");

  const detailQuery = useQuery({
    queryKey: ["kb", "doc", editing ? state.docId : "none"],
    queryFn: () => fetchKBDoc(state.mode === "edit" ? state.docId : ""),
    enabled: editing,
  });

  useEffect(() => {
    const detail = detailQuery.data;
    if (!detail) return;
    setTitle(detail.title);
    setContent(detail.content);
  }, [detailQuery.data]);

  const save = useMutation({
    mutationFn: async (): Promise<KBDocDetail> => {
      if (editing) {
        return updateKBDoc(state.docId, {
          title: title.trim() || undefined,
          content,
        });
      }
      return createKBDoc({
        doc_id: docId.trim(),
        title: title.trim() || undefined,
        content,
      });
    },
    onSuccess: () => {
      toast("已保存。重建索引后即可被 RAG 检索", "ok");
      onSaved();
    },
    onError: (err) => setError(errorMessage(err, "保存失败")),
  });

  const submit = () => {
    setError("");
    if (!editing && !DOC_ID_RE.test(docId.trim())) {
      setError("文档 ID 只能包含小写字母、数字、下划线和连字符，长度 2-64");
      return;
    }
    if (!content.trim()) {
      setError("正文不能为空");
      return;
    }
    save.mutate();
  };

  return (
    <Modal
      title={editing ? `编辑文档 · ${state.docId}` : "新建知识文档"}
      onClose={onClose}
      width={820}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={save.isPending} onClick={submit}>
            {save.isPending ? "保存中…" : "保存文档"}
          </button>
        </>
      }
    >
      {error ? (
        <div className="form-error">
          <AlertTriangle size={14} />
          {error}
        </div>
      ) : null}
      {editing && detailQuery.isLoading ? (
        <div className="skeleton" style={{ height: 60 }} />
      ) : null}
      <div className={editing ? "" : "field-grid"}>
        {editing ? null : (
          <div className="field">
            <label className="label-sm">文档 ID</label>
            <input
              className="input mono"
              value={docId}
              onChange={(event) => setDocId(event.target.value)}
              placeholder="faq"
              autoComplete="off"
            />
            <div className="hint">小写字母、数字、下划线、连字符；落盘为 data/docs/&lt;id&gt;.md</div>
          </div>
        )}
        <div className="field">
          <label className="label-sm">标题（可选）</label>
          <input
            className="input"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="留空则取正文首个标题"
          />
        </div>
      </div>
      <div className="field">
        <label className="label-sm">正文（Markdown）</label>
        <textarea
          className="input kb-editor"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          placeholder="# 标题&#10;&#10;正文内容…"
        />
        <div className="hint">
          {fmtInt(content.length)} 字符 · 保存会生成一个版本，最近保留 20 版
        </div>
      </div>
    </Modal>
  );
}

function VersionsModal({
  docId,
  onClose,
  onRolled,
}: {
  docId: string;
  onClose: () => void;
  onRolled: () => void;
}) {
  const toast = useUI((s) => s.toast);
  const versionsQuery = useQuery({
    queryKey: ["kb", "versions", docId],
    queryFn: () => fetchKBVersions(docId),
  });

  const rollback = useMutation({
    mutationFn: (versionId: string) => rollbackKBDoc(docId, versionId),
    onSuccess: () => {
      toast("已回滚到所选版本，请重建索引", "ok");
      onRolled();
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  return (
    <Modal title={`版本历史 · ${docId}`} onClose={onClose} width={720}>
      {versionsQuery.isLoading ? <div className="skeleton" /> : null}
      <div className="stack" style={{ gap: 8 }}>
        {(versionsQuery.data ?? []).map((version: KBDocVersion, index: number) => (
          <div className="version-item" key={version.id}>
            <span className="tag">{index === 0 ? "当前" : shortId(version.id, 8)}</span>
            <span style={{ flex: 1 }}>
              {version.actor ?? "system"} · {fmtDateTime(version.created_at)}
            </span>
            <span className="mono" style={{ fontSize: 10.5, color: "var(--faint)" }}>
              {shortId(version.sha256, 10)}
            </span>
            <button
              className="btn btn-sm"
              disabled={index === 0 || rollback.isPending}
              onClick={() => rollback.mutate(version.id)}
            >
              回滚
            </button>
          </div>
        ))}
        {(versionsQuery.data ?? []).length === 0 ? (
          <Empty title="暂无版本记录" />
        ) : null}
      </div>
    </Modal>
  );
}
