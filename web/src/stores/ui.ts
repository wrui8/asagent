import { create } from "zustand";

export type Theme = "dark" | "light";
export type ToastTone = "ok" | "warn" | "err" | "";

export interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

const THEME_KEY = "asagent.theme";

function initialTheme(): Theme {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
}

const startTheme = initialTheme();
applyTheme(startTheme);

interface UIState {
  theme: Theme;
  toasts: ToastItem[];
  cmdkOpen: boolean;
  setTheme: (theme: Theme) => void;
  toggleTheme: (origin?: { x: number; y: number }) => void;
  toast: (message: string, tone?: ToastTone) => void;
  dismissToast: (id: number) => void;
  setCmdkOpen: (open: boolean) => void;
}

let toastSeq = 1;
const reduceMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

export const useUI = create<UIState>((set, get) => ({
  theme: startTheme,
  toasts: [],
  cmdkOpen: false,

  setTheme(theme) {
    applyTheme(theme);
    set({ theme });
  },

  toggleTheme(origin) {
    const next: Theme = get().theme === "dark" ? "light" : "dark";
    const apply = () => get().setTheme(next);
    if (!document.startViewTransition || !origin || reduceMotion()) {
      apply();
      return;
    }
    const { x, y } = origin;
    const edge = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y),
    );
    const transition = document.startViewTransition(apply);
    transition.ready
      .then(() => {
        document.documentElement.animate(
          {
            clipPath: [
              `circle(0px at ${x}px ${y}px)`,
              `circle(${edge}px at ${x}px ${y}px)`,
            ],
          },
          {
            duration: 520,
            easing: "ease-in-out",
            pseudoElement: "::view-transition-new(root)",
          },
        );
      })
      .catch(() => undefined);
  },

  toast(message, tone = "") {
    const id = toastSeq++;
    set((state) => ({ toasts: [...state.toasts, { id, message, tone }] }));
    window.setTimeout(() => {
      get().dismissToast(id);
    }, 2600);
  },

  dismissToast(id) {
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
  },

  setCmdkOpen(open) {
    set({ cmdkOpen: open });
  },
}));
