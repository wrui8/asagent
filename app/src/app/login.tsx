import { Redirect, useRouter } from "expo-router";
import { useRef, useState, type ComponentRef } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { errorText } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { API_BASE } from "@/lib/config";
import { colors, radius } from "@/lib/theme";

export default function LoginScreen() {
  const { status, login, register } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const passwordField = useRef<ComponentRef<typeof TextInput>>(null);
  const inviteField = useRef<ComponentRef<typeof TextInput>>(null);
  const submitting = useRef(false);

  if (status === "authed") return <Redirect href="/" />;

  const submit = async () => {
    if (submitting.current) return;
    const name = username.trim();
    if (!name || !password) {
      setError("请输入用户名和密码");
      return;
    }
    if (mode === "register" && !invite.trim()) {
      setError("注册需要邀请码");
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      if (mode === "login") await login(name, password);
      else await register(name, password, invite.trim());
      router.replace("/");
    } catch (err) {
      setError(errorText(err, mode === "register" ? "注册失败" : "登录失败"));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.screen} edges={["top", "bottom", "left", "right"]}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.badge}>
            <Text style={styles.badgeText}>AS</Text>
          </View>
          <Text style={styles.eyebrow}>LEARNING AGENT SYSTEM</Text>
          <Text style={styles.title}>进入控制台</Text>
          <Text style={styles.sub}>
            SQL · RAG · Code · Office 四类任务，实时追踪每一步。
          </Text>

          <View style={styles.form}>
            <Text style={styles.label}>用户名</Text>
            <TextInput
              style={styles.input}
              value={username}
              onChangeText={setUsername}
              placeholder="输入用户名"
              placeholderTextColor={colors.faint}
              autoCapitalize="none"
              autoCorrect={false}
              accessibilityLabel="用户名"
              textContentType="username"
              returnKeyType="next"
              submitBehavior="submit"
              onSubmitEditing={() => passwordField.current?.focus()}
            />

            <Text style={styles.label}>密码</Text>
            <TextInput
              ref={passwordField}
              style={styles.input}
              value={password}
              onChangeText={setPassword}
              placeholder="输入密码"
              placeholderTextColor={colors.faint}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              accessibilityLabel="密码"
              textContentType={mode === "register" ? "newPassword" : "password"}
              returnKeyType={mode === "register" ? "next" : "go"}
              submitBehavior={mode === "register" ? "submit" : "blurAndSubmit"}
              onSubmitEditing={() => {
                if (mode === "register") inviteField.current?.focus();
                else void submit();
              }}
            />

            {mode === "register" ? (
              <>
                <Text style={styles.label}>邀请码</Text>
                <TextInput
                  ref={inviteField}
                  style={styles.input}
                  value={invite}
                  onChangeText={setInvite}
                  placeholder="输入管理员提供的邀请码"
                  placeholderTextColor={colors.faint}
                  autoCapitalize="none"
                  autoCorrect={false}
                  accessibilityLabel="邀请码"
                  returnKeyType="go"
                  onSubmitEditing={() => void submit()}
                />
              </>
            ) : null}

            {error ? <Text style={styles.error}>{error}</Text> : null}

            <Pressable
              accessibilityRole="button"
              style={({ pressed }) => [
                styles.primary,
                (pressed || busy) && styles.primaryPressed,
              ]}
              onPress={() => void submit()}
              disabled={busy}
            >
              {busy ? (
                <ActivityIndicator color="#fff" />
              ) : (
                <Text style={styles.primaryText}>
                  {mode === "login" ? "登录" : "注册并登录"}
                </Text>
              )}
            </Pressable>

            <Pressable
              accessibilityRole="button"
              disabled={busy}
              style={styles.switch}
              onPress={() => {
                setMode(mode === "login" ? "register" : "login");
                setError("");
              }}
            >
              <Text style={styles.switchText}>
                {mode === "login"
                  ? "没有账号？使用邀请码注册"
                  : "已有账号？返回登录"}
              </Text>
            </Pressable>
          </View>

          <Text style={styles.footer}>
            服务地址 · {API_BASE}
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  flex: { flex: 1 },
  scroll: {
    flexGrow: 1,
    justifyContent: "center",
    paddingHorizontal: 28,
    paddingVertical: 32,
  },
  badge: {
    width: 52,
    height: 52,
    borderRadius: 16,
    backgroundColor: colors.accent,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 20,
  },
  badgeText: { color: "#fff", fontSize: 20, fontWeight: "800", letterSpacing: 1 },
  eyebrow: {
    color: colors.accent2,
    fontSize: 10,
    letterSpacing: 2.4,
    fontWeight: "700",
    marginBottom: 8,
  },
  title: { color: colors.text, fontSize: 30, fontWeight: "800" },
  sub: { color: colors.muted, fontSize: 13, lineHeight: 20, marginTop: 10 },
  form: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: 18,
    marginTop: 26,
  },
  label: {
    color: colors.muted,
    fontSize: 12,
    marginBottom: 6,
    marginTop: 12,
  },
  input: {
    backgroundColor: colors.black,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.sm,
    color: colors.text,
    fontSize: 15,
    paddingHorizontal: 12,
    paddingVertical: Platform.OS === "web" ? 12 : 10,
  },
  error: { color: colors.danger, fontSize: 12, marginTop: 12 },
  primary: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 48,
    paddingVertical: 10,
    marginTop: 20,
  },
  primaryPressed: { opacity: 0.75 },
  primaryText: { color: "#fff", fontSize: 15, fontWeight: "700" },
  switch: { alignItems: "center", paddingTop: 16 },
  switchText: { color: colors.accent2, fontSize: 12 },
  footer: {
    color: colors.faint,
    fontSize: 11,
    textAlign: "center",
    marginTop: 24,
  },
});
