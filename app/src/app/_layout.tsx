import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { AuthProvider } from "@/lib/auth";
import { colors } from "@/lib/theme";
import { checkAppUpdate } from "@/lib/update";

export default function RootLayout() {
  useEffect(() => {
    void checkAppUpdate();
  }, []);

  return (
    <SafeAreaProvider>
      <AuthProvider>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerShown: false,
            contentStyle: { backgroundColor: colors.bg },
          }}
        />
      </AuthProvider>
    </SafeAreaProvider>
  );
}
