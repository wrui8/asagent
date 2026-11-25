import { create } from "zustand";
import {
  ApiError,
  fetchMe,
  getAccessToken,
  login as apiLogin,
  logout as apiLogout,
  refreshToken,
  register as apiRegister,
} from "../lib/api";
import type { User } from "../lib/types";
import { callMobile, initializeMobileHistory, IS_MOBILE_HOST, type MobileSession } from "../lib/mobile-host";

type Status = "loading" | "guest" | "authed" | "retry";

let initializedMobileRoute = false;
let mobileAuthVersion = 0;

interface AuthState {
  user: User | null;
  status: Status;
  setUser: (user: User) => void;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, invite: string) => Promise<void>;
  logout: () => Promise<void>;
  bootstrap: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  status: "loading",
  setUser: (user) => set({ user, status: "authed" }),

  async login(username, password) {
    const data = await apiLogin(username, password);
    set({ user: data.user, status: "authed" });
  },

  async register(username, password, invite) {
    const data = await apiRegister(username, password, invite);
    set({ user: data.user, status: "authed" });
  },

  async logout() {
    await apiLogout();
    set({ user: null, status: "guest" });
  },

  async bootstrap() {
    if (IS_MOBILE_HOST) {
      const version = ++mobileAuthVersion;
      set({ status: "loading" });
      try {
        if (!initializedMobileRoute) {
          const path = await callMobile<string>("ready");
          initializeMobileHistory(path);
          initializedMobileRoute = true;
        }
        const session = await callMobile<MobileSession>("bootstrap");
        if (version === mobileAuthVersion) {
          set({ status: session.status === "anon" ? "guest" : session.status, user: session.user });
        }
      } catch {
        if (version === mobileAuthVersion) set({ status: "retry", user: null });
      }
      return;
    }
    try {
      if (!getAccessToken()) {
        const refreshed = await refreshToken();
        if (!refreshed) {
          set({ user: null, status: "guest" });
          return;
        }
      }
      const user = await fetchMe();
      set({ user, status: "authed" });
    } catch (error) {
      if (!(error instanceof ApiError)) {
        // 后端不可达：先按未登录处理，登录页会给出提示
        set({ user: null, status: "guest" });
        return;
      }
      try {
        const refreshed = await refreshToken();
        if (refreshed) {
          const user = await fetchMe();
          set({ user, status: "authed" });
          return;
        }
      } catch {
        /* 刷新失败，回到未登录 */
      }
      set({ user: null, status: "guest" });
    }
  },
}));

window.addEventListener("asagent:logout", () => {
  mobileAuthVersion++;
  useAuth.setState({ user: null, status: "guest" });
});

window.addEventListener("asagent:token", (event) => {
  const detail = (event as CustomEvent).detail;
  if (detail?.user) useAuth.setState({ user: detail.user, status: "authed" });
});
