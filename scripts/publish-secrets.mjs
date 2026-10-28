import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";

let credentials;
try {
  credentials = JSON.parse(readFileSync(".local/credentials.json", "utf8"));
} catch {
  console.error("Run: npm run setup -- proxy.example.com");
  process.exit(1);
}
for (const key of ["SECRET", "BOOT"]) {
  const value = credentials[key];
  if (typeof value !== "string" || !value) {
    console.error("Missing local credential: " + key);
    process.exit(1);
  }
  console.log("Publishing Cloudflare secret binding:", key);
  const windows = process.platform === "win32";
  const result = spawnSync(windows ? "npx.cmd" : "npx", ["wrangler", "secret", "put", key], {
    input: value + "\n",
    stdio: ["pipe", "inherit", "inherit"],
    ...(windows ? { shell: true } : {}),
  });
  if (result.error || result.status !== 0) {
    console.error("Failed to publish " + key + ". Verify Wrangler login and Worker deployment.");
    process.exit(1);
  }
}
console.log("Both secret bindings published. Run npm run probe after binding your hostname.");
