import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { copyText } from "../lib/format";

interface Props {
  content: string;
}

/** 最终回答的 Markdown 渲染（表格、代码、链接）。 */
export function Markdown({ content }: Props) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noreferrer noopener">
            {children}
          </a>
        ),
        pre: ({ children }) => {
          const text = extractText(children);
          return (
            <div style={{ position: "relative" }}>
              <pre>{children}</pre>
              <button
                className="btn btn-sm btn-ghost"
                style={{ position: "absolute", right: 6, top: 6 }}
                onClick={() => void copyText(text)}
              >
                复制
              </button>
            </div>
          );
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

function extractText(node: unknown): string {
  if (typeof node === "string") return node;
  if (typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (node && typeof node === "object" && "props" in node) {
    return extractText((node as { props?: { children?: unknown } }).props?.children);
  }
  return "";
}
