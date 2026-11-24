import { useUI } from "../stores/ui";

export function Toasts() {
  const toasts = useUI((s) => s.toasts);
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.tone}`}>
          <span>●</span>
          <span>{toast.message}</span>
        </div>
      ))}
    </div>
  );
}
