import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";

try {
  const { host, SECRET } = JSON.parse(readFileSync(".local/credentials.json", "utf8"));
  const domain = "https://" + host;
  const signature = createHmac("sha256", Buffer.from(SECRET, "hex"))
    .update(`tdesktop-web-proxy-bridge-v1\n${host}`)
    .digest("base64url");
  const health = await fetch(domain + "/health", { signal: AbortSignal.timeout(10000) });
  const valid = await fetch(domain + "/?bridge=" + signature, { signal: AbortSignal.timeout(10000) });
  const invalid = await fetch(domain + "/?bridge=invalid", { signal: AbortSignal.timeout(10000) });
  const validPage = await valid.text();
  const invalidPage = await invalid.text();
  if (!health.ok || (await health.json()).ok !== true) throw new Error("Health endpoint failed");
  if (!valid.ok || !validPage.includes("TelegramWebProxy")) throw new Error("Authenticated WEB bridge unavailable");
  if (!invalid.ok || invalidPage.includes("TelegramWebProxy")) throw new Error("Unauthorized bridge was disclosed");
  console.log("PASS health, WEB bridge capability, and public camouflage checks.");
  console.log("NOTE: this does NOT test a real Telegram connection or KWS routing.");
} catch (error) {
  console.error("FAIL:", error.message);
  process.exitCode = 1;
}
