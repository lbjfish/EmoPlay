import { gzipSync, brotliCompressSync } from "node:zlib";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  collectInventory,
  loadCommittedInventory,
  normalizeInventory,
  renderNotices,
} from "./generate-license-inventory.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const output = resolve(frontendRoot, "dist");
const expected = [
  "effects.js",
  "game.css",
  "game.js",
  "god-charts.js",
  "index.html",
  "media-review.js",
  "react-core.js",
];
const outputFiles = readdirSync(output).sort();
const fail = (message) => {
  throw new Error(`Production build verification failed: ${message}`);
};

const verifyLicenseInventory = () => {
  let current;
  let committed;
  try {
    current = collectInventory();
    committed = loadCommittedInventory();
  } catch (error) {
    fail(`third-party license inventory cannot be collected: ${error.message}`);
  }
  if (normalizeInventory(current) !== normalizeInventory(committed)) {
    fail("THIRD_PARTY_LICENSES.json is stale");
  }
  const packageJson = JSON.parse(readFileSync(join(frontendRoot, "package.json"), "utf8"));
  const runtimeDependencyCount = Object.keys(packageJson.dependencies ?? {}).length;
  const runtimeDependencies = current.dependencies.filter((item) => item.scope === "runtime");
  if (runtimeDependencies.length !== runtimeDependencyCount) {
    fail("third-party inventory does not cover every runtime dependency");
  }
  for (const item of runtimeDependencies) {
    if (!item.license) {
      fail(`runtime dependency ${item.name} has an empty license field`);
    }
  }
  if (readFileSync(join(frontendRoot, "THIRD_PARTY_NOTICES.md"), "utf8") !== renderNotices(current)) {
    fail("THIRD_PARTY_NOTICES.md is stale");
  }
};

verifyLicenseInventory();

if (JSON.stringify(outputFiles) !== JSON.stringify(expected)) {
  fail(`expected only fixed files ${expected.join(", ")}; got ${outputFiles.join(", ")}`);
}

const bytes = new Map();
for (const name of expected) {
  const path = join(output, name);
  const size = statSync(path).size;
  if (size >= 2_000_000) {
    fail(`${name} exceeds the 2,000,000 byte release-audit limit`);
  }
  bytes.set(name, readFileSync(path));
}

const html = bytes.get("index.html").toString("utf8");
if (!html.includes('name="nining-action-token" content="__NINING_ACTION_TOKEN__"')) {
  fail("index.html no longer preserves the action-token placeholder");
}
if (!html.includes('/assets/game.js') || !html.includes('/assets/game.css')) {
  fail("index.html does not reference the fixed game entry and stylesheet");
}
for (const optionalChunk of ["effects.js", "media-review.js", "god-charts.js"]) {
  if (html.includes(`/assets/${optionalChunk}`)) {
    fail(`index.html preloads optional chunk ${optionalChunk}`);
  }
}
const gameSource = bytes.get("game.js").toString("utf8");
for (const optionalChunk of ["effects.js", "media-review.js", "god-charts.js"]) {
  if (gameSource.includes(`from"./${optionalChunk}"`) || gameSource.includes(`from'./${optionalChunk}'`)) {
    fail(`game.js statically imports optional chunk ${optionalChunk}`);
  }
}

const forbiddenSourceTerms = [
  "sourceMappingURL",
  "/Users/",
  "file:",
  "Bearer ",
  "Authorization:",
];
const nonRequestLibraryUrls = [
  "http://www.w3.org/",
  "https://react.dev/errors/",
  "https://redux.js.org/Errors",
  "https://redux-toolkit.js.org/Errors",
  "https://bit.ly/3cXEKWf",
  "https://photoswipe.com",
];
for (const [name, content] of bytes) {
  const text = content.toString("utf8");
  for (const term of forbiddenSourceTerms) {
    if (text.includes(term)) {
      fail(`${name} contains forbidden text: ${term}`);
    }
  }
  const externalUrls = text.match(/https?:\/\/[^\s"']+/gu) ?? [];
  for (const url of externalUrls) {
    if (!nonRequestLibraryUrls.some((allowed) => url.startsWith(allowed))) {
      fail(`${name} contains an unapproved external HTTP URL`);
    }
  }
}

for (const name of expected.filter((item) => item.endsWith(".js"))) {
  if (bytes.get(name).toString("utf8").includes("NINING_ACTION_TOKEN")) {
    fail(`${name} contains the action-token placeholder`);
  }
}

const sourceRoots = [resolve(frontendRoot, "src"), resolve(frontendRoot, "index.html")];
const dangerousHtmlApi = ["dangerously", "SetInnerHTML"].join("");
const visit = (path) => {
  const stat = statSync(path);
  if (stat.isDirectory()) {
    for (const name of readdirSync(path)) {
      visit(join(path, name));
    }
    return;
  }
  if (![".ts", ".tsx", ".css", ".html"].includes(extname(path))) {
    return;
  }
  if (readFileSync(path, "utf8").includes(dangerousHtmlApi)) {
    fail(`application source uses the unsafe HTML API in ${path}`);
  }
};
for (const root of sourceRoots) {
  visit(root);
}

const compressed = (name, algorithm) => algorithm(bytes.get(name)).byteLength;
const initialGzip = compressed("react-core.js", gzipSync) + compressed("game.js", gzipSync);
const totalGzip = expected
  .filter((name) => name.endsWith(".js"))
  .reduce((sum, name) => sum + compressed(name, gzipSync), 0);
if (initialGzip > 260 * 1024) {
  fail(`initial JavaScript gzip size ${initialGzip} exceeds 260 KiB`);
}
if (compressed("game.css", gzipSync) > 100 * 1024) {
  fail("game.css gzip size exceeds 100 KiB");
}
if (compressed("effects.js", gzipSync) > 120 * 1024) {
  fail("effects.js gzip size exceeds 120 KiB");
}
if (compressed("media-review.js", gzipSync) > 180 * 1024) {
  fail("media-review.js gzip size exceeds 180 KiB");
}
if (compressed("god-charts.js", gzipSync) > 180 * 1024) {
  fail("god-charts.js gzip size exceeds 180 KiB");
}
if (totalGzip > 650 * 1024) {
  fail(`all JavaScript gzip size ${totalGzip} exceeds 650 KiB`);
}

for (const name of expected) {
  const content = bytes.get(name);
  process.stdout.write(
    `${name}: raw=${content.byteLength} gzip=${gzipSync(content).byteLength} br=${brotliCompressSync(content).byteLength}\n`,
  );
}
