import { Redirect, router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { errorText, fetchSessions } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDateTime, fmtMoney, profileLabel, statusMeta } from "@/lib/format";
import { colors, radius } from "@/lib/theme";
import type { Session } from "@/lib/types";

export default function SessionsScreen() {
  const { status } = useAuth();
  if (status === "loading") {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent2} />
      </View>
    );
  }
  if (status === "anon") return <Redirect href="/login" />;
  return <SessionList />;
}

function SessionList() {
  const { user, logout } = useAuth();
  const [items, setItems] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (quiet: boolean) => {
    if (!quiet) setLoading(true);
    try {
      setItems(await fetchSessions());
      setError("");
    } catch (err) {
      setError(errorText(err, "无法加载会话"));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      void load(true);
    }, [load]),
  );

  const open = (id: string) =>
    router.push({ pathname: "/session/[id]", params: { id } });

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.header}>
        <View style={styles.headerText}>
          <Text style={styles.eyebrow}>MISSION CONTROL</Text>
          <Text style={styles.title}>会话控制台</Text>
          <Text style={styles.sub}>{user?.username} · Agent 任务</Text>
        </View>
        <Pressable style={styles.primary} onPress={() => router.push("/new")}>
          <Text style={styles.primaryText}>＋ 新建</Text>
        </Pressable>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {loading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent2} />
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(item) => item.session_id}
          contentContainerStyle={styles.list}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={() => {
                setRefreshing(true);
                void load(true);
              }}
              tintColor={colors.accent2}
            />
          }
          ListEmptyComponent={
            <Text style={styles.empty}>还没有会话，点击右上角新建。</Text>
          }
          ListFooterComponent={
            <Pressable style={styles.logout} onPress={() => void logout()}>
              <Text style={styles.logoutText}>退出登录</Text>
            </Pressable>
          }
          renderItem={({ item }) => {
            const meta = statusMeta(item.status);
            return (
              <Pressable
                style={({ pressed }) => [styles.card, pressed && styles.cardPressed]}
                onPress={() => open(item.session_id)}
              >
                <View style={styles.cardTop}>
                  <View style={styles.profileTag}>
                    <Text style={styles.profileText}>
                      {profileLabel(item.profile)}
                    </Text>
                  </View>
                  <View style={[styles.statusDot, { backgroundColor: meta.color }]} />
                  <Text style={[styles.statusText, { color: meta.color }]}>
                    {meta.label}
                  </Text>
                  <Text style={styles.cost}>{fmtMoney(item.cost)}</Text>
                </View>
                <Text style={styles.cardTitle} numberOfLines={2}>
                  {item.title || item.final_answer || item.session_id}
                </Text>
                <Text style={styles.cardMeta}>
                  {fmtDateTime(item.updated_at)} · {item.step} 步 ·{" "}
                  {item.session_id.slice(0, 10)}
                </Text>
              </Pressable>
            );
          }}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 20,
    paddingTop: 18,
    paddingBottom: 14,
    gap: 12,
  },
  headerText: { flex: 1 },
  eyebrow: {
    color: colors.accent2,
    fontSize: 10,
    letterSpacing: 2,
    fontWeight: "700",
    marginBottom: 4,
  },
  title: { color: colors.text, fontSize: 26, fontWeight: "800" },
  sub: { color: colors.muted, fontSize: 12, marginTop: 4 },
  primary: {
    backgroundColor: colors.accent,
    borderRadius: radius.md,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  primaryText: { color: "#fff", fontWeight: "700", fontSize: 14 },
  list: { paddingHorizontal: 16, paddingBottom: 32, gap: 12 },
  card: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: 16,
  },
  cardPressed: { opacity: 0.75 },
  cardTop: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 },
  profileTag: {
    backgroundColor: colors.raised,
    borderRadius: 6,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  profileText: { color: colors.muted, fontSize: 11 },
  statusDot: { width: 6, height: 6, borderRadius: 3, marginLeft: 4 },
  statusText: { fontSize: 11, fontWeight: "600" },
  cost: { marginLeft: "auto", color: colors.text, fontSize: 13, fontWeight: "700" },
  cardTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "600",
    marginTop: 10,
    lineHeight: 21,
  },
  cardMeta: { color: colors.faint, fontSize: 11, marginTop: 8 },
  empty: {
    color: colors.muted,
    textAlign: "center",
    marginTop: 60,
    fontSize: 13,
  },
  error: { color: colors.danger, fontSize: 12, paddingHorizontal: 20, paddingBottom: 8 },
  logout: { alignItems: "center", paddingVertical: 24 },
  logoutText: { color: colors.faint, fontSize: 13 },
});
