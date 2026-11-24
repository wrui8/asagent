import { useEffect, useRef } from "react";

const reduceMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** 深空背景层：星点、网格、地平面、光晕与跟随光斑。 */
export function Background() {
  const glowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (reduceMotion()) return;
    let frame = 0;
    const onMove = (event: PointerEvent) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const el = glowRef.current;
        if (!el) return;
        el.style.transform = `translate(${event.clientX - 230}px, ${event.clientY - 230}px)`;
      });
    };
    window.addEventListener("pointermove", onMove);
    return () => {
      window.removeEventListener("pointermove", onMove);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return (
    <>
      <div className="bg-glow a" />
      <div className="bg-glow b" />
      <div className="bg-glow c" />
      <div className="bg-grid" />
      <div className="bg-stars" />
      <div className="bg-floor" />
      <div className="cursor-glow" ref={glowRef} />
    </>
  );
}
