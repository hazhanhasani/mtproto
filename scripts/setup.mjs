import { createHmac, randomBytes } from "node:crypto";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const host = process.argv[2]?.trim();
const rotate = process.argv.includes("--rotate");
function canonicalHost(input) {
  if (!input || input !== input.toLowerCase() || input.length > 253 || !input.includes(".")) {
    throw new Error("Provide a lowercase ASCII DNS hostname (without https:// or a port).");
  }
  const names = input.split(".");
  if (names.some((name) => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(name))) {
    throw new Error("Invalid DNS hostname.");
  }
  return input;
}
try {
  const domain = canonicalHost(host);
  const root = join(process.cwd(), ".local");
  const path = join(root, "credentials.json");
  if (existsSync(path) && !rotate) {
    throw new Error("Existing credentials found; use --rotate ONLY when intentionally replacing the deployed proxy secret.");
  }
  mkdirSync(root, { recursive: true, mode: 0o700 });
  const secret = randomBytes(16).toString("hex");
  const boot = randomBytes(32).toString("hex");
  const config = { host: domain, SECRET: secret, BOOT: boot };
  writeFileSync(path, JSON.stringify(config, null, 2) + "\n", { mode: 0o600 });
  writeFileSync(join(process.cwd(), ".dev.vars"), `SECRET=${secret}\nBOOT=${boot}\n`, { mode: 0o600 });
  const bridge = createHmac("sha256", Buffer.from(secret, "hex"))
    .update(`tdesktop-web-proxy-bridge-v1\n${domain}`)
    .digest("base64url");
  const params = new URLSearchParams({ server: domain, secret });
  const tgLink = `tg://webproxy?${params}`;
  const webLink = `https://t.me/webproxy?${params}`;
  console.log("WEB proxy credentials created locally (never commit .local or .dev.vars).");
  console.log("Host:       ", domain);
  console.log("WEB proxy:  ", tgLink);
  console.log("Share URL:  ", webLink);
  console.log("Bridge test:", `https://${domain}/?bridge=${bridge}`);
  console.log("\nNext: npm run deploy; npm run publish-secrets; attach the Cloudflare custom domain; npm run probe.");
  console.log("Rotate with care: changing SECRET invalidates every existing client link.");
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
