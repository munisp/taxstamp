import * as SecureStore from "expo-secure-store";
import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { fetchCapabilities, fetchReadiness, type Capability } from "../../clients/typescript/taxstamp-contracts";

const API_URL_KEY = "taxstamp.field.api-url";

type ConnectionState = "disconnected" | "checking" | "ready" | "unavailable";

export default function App() {
  const [apiUrl, setApiUrl] = useState("");
  const [connection, setConnection] = useState<ConnectionState>("disconnected");
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [serial, setSerial] = useState("");
  const [code, setCode] = useState("");

  useEffect(() => {
    void SecureStore.getItemAsync(API_URL_KEY).then((value) => {
      if (value) setApiUrl(value);
    });
  }, []);

  async function inspectConnection(): Promise<void> {
    setConnection("checking");
    try {
      await SecureStore.setItemAsync(API_URL_KEY, apiUrl.trim());
      await fetchReadiness(apiUrl);
      setCapabilities(await fetchCapabilities(apiUrl));
      setConnection("ready");
    } catch (error) {
      setConnection("unavailable");
      Alert.alert("Endpoint unavailable", error instanceof Error ? error.message : "Check HTTPS gateway and network access.");
    }
  }

  function beginVerification(): void {
    Alert.alert(
      "Secure device signing required",
      "Field verification will be enabled after device identity, Keycloak policy, and the gateway signing path are provisioned. This app deliberately does not contain a device HMAC secret."
    );
  }

  const implemented = capabilities.filter((capability) => capability.state === "implemented").length;

  return (
    <SafeAreaView style={styles.safeArea}>
      <StatusBar style="dark" />
      <ScrollView contentContainerStyle={styles.page}>
        <View style={styles.wordmarkRow}>
          <View style={styles.seal}><View style={styles.sealCore} /></View>
          <View><Text style={styles.wordmark}>TAXSTAMP</Text><Text style={styles.subtitle}>FIELD EVIDENCE</Text></View>
        </View>

        <View style={styles.hero}>
          <Text style={styles.kicker}>DEVICE WORKSPACE</Text>
          <Text style={styles.title}>Verify with proof, not inference.</Text>
          <Text style={styles.description}>A secure mobile companion for inspectors and operators. Connection and capability evidence are read from the Taxstamp API; field signing remains outside the application binary.</Text>
          <View style={[styles.connectionPill, connection === "ready" ? styles.readyPill : styles.pendingPill]}>
            {connection === "checking" ? <ActivityIndicator color="#0B6B62" size="small" /> : <View style={[styles.dot, connection === "ready" ? styles.dotReady : styles.dotPending]} />}
            <Text style={styles.connectionText}>{connection === "ready" ? "Verified endpoint" : connection === "checking" ? "Checking endpoint" : "Endpoint not verified"}</Text>
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardLabel}>HTTPS API ENDPOINT</Text>
          <TextInput value={apiUrl} onChangeText={setApiUrl} autoCapitalize="none" autoCorrect={false} keyboardType="url" placeholder="https://api.example.ng" placeholderTextColor="#85918B" style={styles.input} />
          <Pressable style={styles.primaryButton} onPress={() => void inspectConnection()} disabled={connection === "checking"}>
            <Text style={styles.primaryButtonText}>{connection === "checking" ? "VERIFYING…" : "SAVE & VERIFY"}</Text>
          </Pressable>
          <Text style={styles.note}>The endpoint URL is stored in encrypted device storage. No bearer token or signing secret is retained here.</Text>
        </View>

        <View style={styles.rule} />
        <View style={styles.sectionHeading}><Text style={styles.kicker}>FIELD CHECK</Text><Text style={styles.sectionTitle}>Stamp verification</Text></View>
        <View style={styles.card}>
          <TextInput value={serial} onChangeText={setSerial} placeholder="Stamp serial" placeholderTextColor="#85918B" autoCapitalize="characters" style={styles.input} />
          <TextInput value={code} onChangeText={setCode} placeholder="Secure code" placeholderTextColor="#85918B" autoCapitalize="characters" style={styles.input} />
          <Pressable style={[styles.primaryButton, connection !== "ready" ? styles.disabledButton : null]} onPress={beginVerification}>
            <Text style={styles.primaryButtonText}>REQUEST SECURE VERIFICATION</Text>
          </Pressable>
          <Text style={styles.note}>Camera scanning and signed request transport are intentionally gated on device credential provisioning and mobile security review.</Text>
        </View>

        <View style={styles.sectionHeading}><Text style={styles.kicker}>API EVIDENCE</Text><Text style={styles.sectionTitle}>Capability record</Text></View>
        <View style={styles.capabilityList}>
          {connection !== "ready" ? <Text style={styles.empty}>Connect a verified endpoint to inspect its declared capability contract.</Text> : capabilities.slice(0, 6).map((capability) => <View key={capability.name} style={styles.capability}><View style={[styles.dot, capability.state === "implemented" ? styles.dotReady : styles.dotPending]} /><View style={styles.capabilityText}><Text style={styles.capabilityName}>{capability.name.replaceAll("_", " ")}</Text><Text style={styles.capabilityDetail}>{capability.detail}</Text></View></View>)}
        </View>
        {connection === "ready" && <Text style={styles.footerEvidence}>{implemented} implemented states reported by the connected API.</Text>}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: "#F4F2EC" }, page: { padding: 22, gap: 18 },
  wordmarkRow: { flexDirection: "row", gap: 11, alignItems: "center", marginBottom: 3 }, seal: { width: 39, height: 39, borderRadius: 20, backgroundColor: "#0B6B62", alignItems: "center", justifyContent: "center", borderWidth: 6, borderColor: "#DDE7DF" }, sealCore: { width: 9, height: 9, transform: [{ rotate: "45deg" }], backgroundColor: "#F4F2EC" }, wordmark: { letterSpacing: 1.8, fontWeight: "800", fontSize: 15, color: "#17322E" }, subtitle: { fontSize: 9, letterSpacing: 1.4, color: "#6B7B74", fontWeight: "700", marginTop: 2 },
  hero: { paddingVertical: 18 }, kicker: { color: "#0B6B62", fontSize: 10, letterSpacing: 1.4, fontWeight: "800" }, title: { color: "#182422", fontSize: 37, lineHeight: 39, fontWeight: "700", letterSpacing: -1.1, marginTop: 7 }, description: { color: "#56665F", fontSize: 14, lineHeight: 21, marginTop: 12 }, connectionPill: { alignSelf: "flex-start", flexDirection: "row", alignItems: "center", gap: 8, borderRadius: 4, paddingHorizontal: 10, paddingVertical: 8, marginTop: 16 }, readyPill: { backgroundColor: "#DCECE2" }, pendingPill: { backgroundColor: "#F0E6D0" }, connectionText: { color: "#29463D", fontSize: 11, fontWeight: "800", textTransform: "uppercase", letterSpacing: .8 }, dot: { width: 7, height: 7, borderRadius: 7 }, dotReady: { backgroundColor: "#0B6B62" }, dotPending: { backgroundColor: "#B97826" },
  card: { backgroundColor: "#FBFAF5", padding: 16, borderWidth: 1, borderColor: "#D2D7CE", gap: 11 }, cardLabel: { color: "#6B7B74", fontSize: 10, letterSpacing: 1.2, fontWeight: "800" }, input: { minHeight: 46, borderWidth: 1, borderColor: "#BFC9BF", color: "#182422", backgroundColor: "#FFFFFF", paddingHorizontal: 12, fontSize: 14 }, primaryButton: { minHeight: 46, justifyContent: "center", alignItems: "center", backgroundColor: "#0B6B62", paddingHorizontal: 14 }, disabledButton: { opacity: .76 }, primaryButtonText: { color: "#FFFFFF", fontSize: 11, letterSpacing: 1, fontWeight: "800" }, note: { color: "#6A7771", fontSize: 11, lineHeight: 16 },
  rule: { height: 1, backgroundColor: "#CBD1C8", marginVertical: 3 }, sectionHeading: { marginTop: 5 }, sectionTitle: { color: "#182422", fontSize: 24, fontWeight: "700", letterSpacing: -.5, marginTop: 3 }, capabilityList: { backgroundColor: "#FBFAF5", borderTopWidth: 1, borderTopColor: "#D2D7CE" }, capability: { flexDirection: "row", gap: 10, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: "#D8DDD5", alignItems: "flex-start" }, capabilityText: { flex: 1 }, capabilityName: { color: "#263D35", fontSize: 13, textTransform: "capitalize", fontWeight: "800" }, capabilityDetail: { color: "#697870", fontSize: 11, lineHeight: 16, marginTop: 3 }, empty: { color: "#68776F", fontSize: 13, lineHeight: 19, paddingVertical: 16 }, footerEvidence: { color: "#50655C", fontSize: 11, marginTop: -7 },
});
