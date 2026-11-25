import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Alert } from "react-native";

import {
  bootstrap,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  setExpiredHandler,
  type BootstrapResult,
} from "./api";
import type { User } from "./types";

type Status = "loading" | "anon" | "authed";

interface AuthValue {
  status: Status;
  user: User | null;
  restoreFailed: boolean;
  retryRestore: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, invite: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<BootstrapResult | { status: "loading"; user: null }>({
    status: "loading",
    user: null,
  });
  const operation = useRef(0);

  const restore = useCallback(async () => {
    const current = ++operation.current;
    const result = await bootstrap();
    if (current === operation.current) setState(result);
  }, []);
  const cancelPending = useCallback(() => { operation.current++; }, []);

  const retryRestore = useCallback(async () => {
    setState({ status: "loading", user: null });
    await restore();
  }, [restore]);

  useEffect(() => {
    void restore();
    return cancelPending;
  }, [restore, cancelPending]);

  useEffect(() => {
    setExpiredHandler(() => {
      cancelPending();
      setState({ status: "anon", user: null });
    });
    return () => setExpiredHandler(null);
  }, [cancelPending]);

  const login = useCallback(async (username: string, password: string) => {
    const current = ++operation.current;
    const data = await apiLogin(username, password);
    if (current === operation.current) setState({ status: "authed", user: data.user });
  }, []);

  const register = useCallback(
    async (username: string, password: string, invite: string) => {
      const current = ++operation.current;
      const data = await apiRegister(username, password, invite);
      if (current === operation.current) setState({ status: "authed", user: data.user });
    },
    [],
  );

  const logout = useCallback(async () => {
    const current = ++operation.current;
    const signedOut = await apiLogout();
    if (current !== operation.current) return;
    if (signedOut) setState({ status: "anon", user: null });
    else Alert.alert("无法退出登录", "请检查网络连接后重试。");
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      status: state.status === "retry" ? "loading" : state.status,
      user: state.user,
      restoreFailed: state.status === "retry",
      retryRestore,
      login,
      register,
      logout,
    }),
    [state, retryRestore, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return value;
}
