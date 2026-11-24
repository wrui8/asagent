import type { SessionStatus } from "../lib/types";
import { statusMeta } from "../lib/format";

export function StatusBadge({ status }: { status: SessionStatus | string }) {
  const meta = statusMeta(status);
  return (
    <span className={`badge ${meta.cls}`}>
      <span className="dot" />
      {meta.label}
    </span>
  );
}

const PERMISSION_LABEL = ["READ", "WRITE", "DANGEROUS"];

export function PermissionBadge({ permission }: { permission: number }) {
  const label = PERMISSION_LABEL[permission] ?? `L${permission}`;
  const cls = permission >= 2 ? "badge-fail" : permission === 1 ? "badge-wait" : "badge-mute";
  return <span className={`badge ${cls}`}>{label}</span>;
}

export function ProfileTag({ profile }: { profile: string }) {
  return <span className="tag">{profile}</span>;
}
