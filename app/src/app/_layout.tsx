import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { Platform, Pressable, StyleSheet, Text } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";

import { AuthProvider, useAuth } from "@/lib/auth";
import { colors } from "@/lib/theme";
import { checkAppUpdate } from "@/lib/update";
import ConsoleHost from "../console/ConsoleHost";

export default function RootLayout() {
  useEffect(() => {
    void checkAppUpdate();
  }, []);

  return (
    <SafeAreaProvider>
      {Platform.OS === "android" ? (
        <AuthProvider>
          <StatusBar style="light" />
          <AppStack />
        </AuthProvider>
      ) : <ConsoleHost />}
    </SafeAreaProvider>
  );
}

function AppStack() {
  const { restoreFailed, retryRestore } = useAuth();

  return (
    <>
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: colors.bg },
        }}
      />
      {restoreFailed && (
        <SafeAreaView style={styles.restore}>
          <Text accessibilityRole="alert" style={styles.title}>暂时无法恢复登录</Text>
          <Text style={styles.message}>请检查网络连接后重试。</Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => void retryRestore()}
            style={({ pressed }) => [styles.retry, pressed && styles.pressed]}
          >
            <Text style={styles.retryLabel}>重试</Text>
          </Pressable>
        </SafeAreaView>
      )}
    </>
  );
}

const styles = StyleSheet.create({
  restore: {
    ...StyleSheet.absoluteFill,
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    paddingHorizontal: 24,
    backgroundColor: colors.bg,
  },
  title: { color: colors.text, fontSize: 20, fontWeight: "700", textAlign: "center" },
  message: { color: colors.muted, fontSize: 15, textAlign: "center" },
  retry: {
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 24,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: colors.raised,
  },
  pressed: { opacity: 0.7 },
  retryLabel: { color: colors.text, fontSize: 16, fontWeight: "600" },
});
