import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  bootstrap,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  setExpiredHandler,
} from "./api";
import type { User } from "./types";

type Status = "loading" | "anon" | "authed";

interface AuthValue {
  status: Status;
  user: User | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, invite: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ status: Status; user: User | null }>({
    status: "loading",
    user: null,
  });

  useEffect(() => {
    let alive = true;
    void (async () => {
      const user = await bootstrap();
      if (!alive) return;
      setState(
        user ? { status: "authed", user } : { status: "anon", user: null },
      );
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    setExpiredHandler(() => setState({ status: "anon", user: null }));
    return () => setExpiredHandler(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const data = await apiLogin(username, password);
    setState({ status: "authed", user: data.user });
  }, []);

  const register = useCallback(
    async (username: string, password: string, invite: string) => {
      const data = await apiRegister(username, password, invite);
      setState({ status: "authed", user: data.user });
    },
    [],
  );

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setState({ status: "anon", user: null });
    }
  }, []);

  const value = useMemo(
    () => ({ ...state, login, register, logout }),
    [state, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return value;
}
