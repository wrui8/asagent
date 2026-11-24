import { useRef, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  className?: string;
  max?: number;
}

/** 指针驱动的 3D 倾斜；离开后缓动回正。 */
export function Tilt({ children, className = "", max = 6 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const enabled = !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  const onMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el || !enabled || event.pointerType !== "mouse") return;
    const rect = el.getBoundingClientRect();
    const px = (event.clientX - rect.left) / rect.width - 0.5;
    const py = (event.clientY - rect.top) / rect.height - 0.5;
    el.style.transition = "transform .12s ease-out";
    el.style.transform = `perspective(900px) rotateX(${(-py * max).toFixed(
      2,
    )}deg) rotateY(${(px * (max + 2)).toFixed(2)}deg) translateY(-3px)`;
  };

  const onLeave = () => {
    const el = ref.current;
    if (!el) return;
    el.style.transition = "transform .45s cubic-bezier(.2,.7,.2,1)";
    el.style.transform = "";
  };

  return (
    <div
      ref={ref}
      className={`tilt ${className}`}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
    >
      {children}
    </div>
  );
}
