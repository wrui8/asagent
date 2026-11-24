import { Redirect, useRouter } from "expo-router";
import { useCallback, useEffect, useState } from "react";
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

import { createSession, errorText, fetchProfiles } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtMoney, profileLabel } from "@/lib/format";
import { colors, radius } from "@/lib/theme";
import type { ProfileInfo } from "@/lib/types";

const PRESETS: Record<string, { placeholder: string; hint: string }> = {
  sql: {
    placeholder: "例如：统计销售额最高的 5 个品类",
    hint: "只读 SQL 分析，自动生成查询并给出结论。",
  },
  rag: {
    placeholder: "例如：总结知识库里关于权限模型的说明",
    hint: "基于本地知识库检索，引用原文作答。",
  },
  code: {
    placeholder: "例如：写一个 Python 函数把 CSV 转成 JSON",
    hint: "沙箱内读写代码并运行测试。",
  },
  office: {
    placeholder: "例如：生成一份本周工作周报并落盘",
    hint: "文档生成需要人工审批后才写入。",
  },
};

const FALLBACK: { placeholder: string; hint: string } = {
  placeholder: "描述你要完成的任务",
  hint: "Agent 会规划步骤并在需要时请求审批。",
};

export default function NewSessionScreen() {
  const { status } = useAuth();
  if (status === "loading") {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent2} />
      </View>
    );
  }
  if (status === "anon") return <Redirect href="/login" />;
  return <NewSessionForm />;
}

function NewSessionForm() {
  const router = useRouter();
  const [profiles, setProfiles] = useState<ProfileInfo[]>([]);
  const [profile, setProfile] = useState("sql");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const list = await fetchProfiles();
        if (!alive) return;
        setProfiles(list);
        if (list.length && !list.some((item) => item.name === profile)) {
          setProfile(list[0].name);
        }
      } catch (err) {
        if (alive) setError(errorText(err, "无法加载场景"));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // profile 仅用于初始选择，不参与重新拉取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = profiles.find((item) => item.name === profile);
  const preset = PRESETS[profile] ?? FALLBACK;

  const create = useCallback(async () => {
    const text = input.trim();
    if (!text) {
      setError("请先描述任务");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const data = await createSession({
        profile,
        input: text,
        max_steps: 12,
        max_cost_cny: selected?.max_cost_cny ?? null,
      });
      router.replace({
        pathname: "/session/[id]",
        params: { id: data.session_id },
      });
    } catch (err) {
      setError(errorText(err, "创建会话失败"));
      setBusy(false);
    }
  }, [input, profile, selected, router]);

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
          <View style={styles.topBar}>
            <Pressable onPress={() => router.back()} style={styles.back}>
              <Text style={styles.backText}>‹ 返回</Text>
            </Pressable>
            <Text style={styles.topTitle}>新建任务</Text>
            <View style={styles.back} />
          </View>

          <Text style={styles.eyebrow}>STEP 1 · 选择场景</Text>
          {loading ? (
            <ActivityIndicator color={colors.accent2} style={styles.loading} />
          ) : (
            <View style={styles.profiles}>
              {profiles.map((item) => {
                const active = item.name === profile;
                return (
                  <Pressable
                    key={item.name}
                    style={[styles.profile, active && styles.profileActive]}
                    onPress={() => setProfile(item.name)}
                  >
                    <Text
                      style={[
                        styles.profileName,
                        active && styles.profileNameActive,
                      ]}
                    >
                      {profileLabel(item.name)}
                    </Text>
                    <Text style={styles.profileMeta}>
                      权限 {item.max_permission} · 上限{" "}
                      {item.max_cost_cny ? fmtMoney(item.max_cost_cny) : "不限"}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          )}

          <Text style={styles.eyebrow}>STEP 2 · 描述任务</Text>
          <Text style={styles.hint}>{preset.hint}</Text>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder={preset.placeholder}
            placeholderTextColor={colors.faint}
            multiline
            textAlignVertical="top"
          />

          {error ? <Text style={styles.error}>{error}</Text> : null}

          <Pressable
            style={({ pressed }) => [
              styles.primary,
              (pressed || busy) && styles.primaryPressed,
            ]}
            onPress={() => void create()}
            disabled={busy}
          >
            {busy ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.primaryText}>启动 Agent</Text>
            )}
          </Pressable>
          <Text style={styles.notice}>
            脚本与文档写入会进入人工审批，审批在手机端完成。
          </Text>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  flex: { flex: 1 },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
  },
  scroll: { paddingHorizontal: 20, paddingBottom: 40 },
  topBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 14,
  },
  back: { width: 64 },
  backText: { color: colors.accent2, fontSize: 14 },
  topTitle: { color: colors.text, fontSize: 16, fontWeight: "700" },
  eyebrow: {
    color: colors.faint,
    fontSize: 10,
    letterSpacing: 1.6,
    fontWeight: "700",
    marginTop: 18,
    marginBottom: 10,
  },
  loading: { marginVertical: 20 },
  profiles: { gap: 8 },
  profile: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  profileActive: { borderColor: colors.accent, backgroundColor: colors.raised },
  profileName: { color: colors.text, fontSize: 14, fontWeight: "600" },
  profileNameActive: { color: "#fff" },
  profileMeta: { color: colors.faint, fontSize: 11, marginTop: 4 },
  hint: { color: colors.muted, fontSize: 12, marginBottom: 10 },
  input: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    color: colors.text,
    fontSize: 15,
    lineHeight: 22,
    minHeight: 132,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  error: { color: colors.danger, fontSize: 12, marginTop: 10 },
  primary: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    alignItems: "center",
    justifyContent: "center",
    height: 50,
    marginTop: 22,
  },
  primaryPressed: { opacity: 0.75 },
  primaryText: { color: "#fff", fontSize: 15, fontWeight: "700" },
  notice: {
    color: colors.faint,
    fontSize: 11,
    textAlign: "center",
    marginTop: 12,
  },
});
