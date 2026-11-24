import type { ReactNode } from "react";

interface Props {
  icon?: ReactNode;
  title?: string;
  children?: ReactNode;
}

export function Empty({ icon, title, children }: Props) {
  return (
    <div className="empty">
      {icon ? <div className="icon">{icon}</div> : null}
      {title ? <div style={{ color: "var(--muted)", fontWeight: 700 }}>{title}</div> : null}
      {children}
    </div>
  );
}
