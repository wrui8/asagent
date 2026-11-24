import { describeEvent } from "../lib/events";
import { fmtClock } from "../lib/format";
import type { AgentEvent } from "../lib/types";

interface Props {
  events: AgentEvent[];
  live?: boolean;
}

/** 执行轨迹时间线。 */
export function Timeline({ events, live }: Props) {
  if (!events.length && !live) {
    return <div className="empty">暂无事件</div>;
  }
  return (
    <div className="tl">
      {events.map((event) => {
        const meta = describeEvent(event);
        return (
          <div key={event.seq} className={`tl-item ${meta.cls}`}>
            <div className="tl-dot" />
            <div className="tl-top">
              <span className="tl-type">{meta.label}</span>
              <span className="tl-time">{fmtClock(event.ts)}</span>
              {meta.tag ? <span className="tag">{meta.tag}</span> : null}
            </div>
            {meta.detail ? <div className="tl-detail">{meta.detail}</div> : null}
          </div>
        );
      })}
      {live ? (
        <div className="tl-item live">
          <div className="tl-dot" />
          <div className="tl-top">
            <span className="tl-type">listening</span>
            <span className="tl-time">…</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
