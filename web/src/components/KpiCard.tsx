import type { ReactNode } from "react";
import { CountUp } from "./CountUp";

interface Props {
  label: string;
  value: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  sub?: ReactNode;
  valueStyle?: React.CSSProperties;
  icon?: ReactNode;
}

/** KPI 卡：HUD 角标 + 光晕 + 数字滚动（倾斜由外层 Tilt 提供）。 */
export function KpiCard({
  label,
  value,
  decimals = 0,
  prefix,
  suffix,
  sub,
  valueStyle,
  icon,
}: Props) {
  return (
    <div className="card kpi lift hud">
      <div className="halo" />
      <div className="label">
        {icon}
        {label}
      </div>
      <div className="value" style={valueStyle}>
        <CountUp value={value} decimals={decimals} prefix={prefix} suffix={suffix} />
      </div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}
