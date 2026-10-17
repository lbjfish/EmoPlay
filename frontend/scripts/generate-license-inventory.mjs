import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, "..");
const packageJsonPath = join(frontendRoot, "package.json");
const lockfilePath = join(frontendRoot, "pnpm-lock.yaml");
const inventoryPath = join(frontendRoot, "THIRD_PARTY_LICENSES.json");
const noticesPath = join(frontendRoot, "THIRD_PARTY_NOTICES.md");

const runtimeUses = new Map([
  ["@radix-ui/react-alert-dialog", "Focus-managed destructive-action confirmations"],
  ["@radix-ui/react-dialog", "Focus-managed media and review dialogs"],
  ["@tsparticles/react", "Optional reduced-motion-aware rain atmosphere integration"],
  ["@tsparticles/slim", "Optional particle-engine features for the atmosphere"],
  ["embla-carousel-react", "Memory-card carousel controls"],
  ["lucide-react", "Interface icon components"],
  ["motion", "Optional animation enhancement"],
  ["photoswipe", "Optional image review enhancement"],
  ["react", "Browser UI rendering"],
  ["react-dom", "Browser DOM rendering"],
  ["recharts", "Optional director metrics charts"],
  ["wavesurfer.js", "Optional audio review waveform"],
]);

const readJson = (path) => JSON.parse(readFileSync(path, "utf8"));
const sha256 = (content) => createHash("sha256").update(content).digest("hex");

const normalizeLicense = (license) => {
  if (typeof license === "string") {
    return license.trim();
  }
  if (license && typeof license === "object" && typeof license.type === "string") {
    return license.type.trim();
  }
  return "";
};

const lockEntryFor = (lockfile, name, specifier) => {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const spec = specifier.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = lockfile.match(
    new RegExp(`^      ['"]?${escaped}['"]?:\\n        specifier: ${spec}\\n        version: ([^\\n]+)$`, "m"),
  );
  return match?.[1] ?? "";
};

const packageMetadata = (name) => {
  const metadataPath = join(frontendRoot, "node_modules", name, "package.json");
  if (!existsSync(metadataPath)) {
    throw new Error(`installed package metadata is missing for ${name}`);
  }
  const metadata = readJson(metadataPath);
  return {
    resolvedName: metadata.name,
    resolvedVersion: metadata.version,
    license: normalizeLicense(metadata.license),
  };
};

const collectDirectDependencies = (packageJson, lockfile) => {
  const scopes = [
    ["runtime", packageJson.dependencies ?? {}],
    ["development", packageJson.devDependencies ?? {}],
  ];
  const dependencies = [];
  for (const [scope, entries] of scopes) {
    for (const [name, specifier] of Object.entries(entries).sort(([a], [b]) => a.localeCompare(b))) {
      if (typeof specifier !== "string") {
        throw new Error(`dependency specifier for ${name} must be a string`);
      }
      const lockedVersion = lockEntryFor(lockfile, name, specifier);
      if (!lockedVersion) {
        throw new Error(`lockfile entry is missing for direct dependency ${name}@${specifier}`);
      }
      const metadata = packageMetadata(name);
      if (!metadata.resolvedVersion) {
        throw new Error(`installed package version is missing for ${name}`);
      }
      if (scope === "runtime" && !metadata.license) {
        throw new Error(`runtime dependency ${name} has an empty license field`);
      }
      dependencies.push({
        name,
        scope,
        specifier,
        lockedVersion,
        installedVersion: metadata.resolvedVersion,
        license: metadata.license,
        usedFor: scope === "runtime" ? runtimeUses.get(name) ?? "Runtime browser functionality" : "Build and test tooling",
      });
    }
  }
  return dependencies;
};

export const collectInventory = () => {
  const packageJson = readJson(packageJsonPath);
  const lockfile = readFileSync(lockfilePath, "utf8");
  const dependencies = collectDirectDependencies(packageJson, lockfile);
  const packageManager = packageJson.packageManager;
  if (typeof packageManager !== "string" || !packageManager.startsWith("pnpm@")) {
    throw new Error("packageManager must pin pnpm");
  }
  return {
    generatedBy: "frontend/scripts/generate-license-inventory.mjs",
    schemaVersion: 1,
    packageName: packageJson.name,
    packageVersion: packageJson.version,
    packageManager,
    lockfile: {
      path: "pnpm-lock.yaml",
      sha256: sha256(lockfile),
    },
    directDependencyCount: dependencies.length,
    runtimeDependencyCount: dependencies.filter((item) => item.scope === "runtime").length,
    dependencies,
  };
};

export const normalizeInventory = (inventory) => `${JSON.stringify(inventory, null, 2)}\n`;

export const renderNotices = (inventory) => {
  const lines = [
    "# Third-party notices",
    "",
    "This browser bundle is self-hosted and does not intentionally load packages, fonts, icons, telemetry, or media from a remote origin.",
    "",
    "The machine-readable inventory is `THIRD_PARTY_LICENSES.json`. It is generated from `package.json`, `pnpm-lock.yaml`, and the installed direct packages' own `package.json` metadata.",
    "",
    `- Package manager: \`${inventory.packageManager}\``,
    `- Lockfile SHA-256: \`${inventory.lockfile.sha256}\``,
    `- Direct dependencies: ${inventory.directDependencyCount}`,
    `- Runtime dependencies with non-empty license metadata: ${inventory.runtimeDependencyCount}`,
    "",
    "| Package | Scope | Specifier | Locked version | Installed version | License | Used for |",
    "| --- | --- | ---: | --- | ---: | --- | --- |",
  ];
  for (const item of inventory.dependencies) {
    lines.push(
      `| ${item.name} | ${item.scope} | ${item.specifier} | ${item.lockedVersion} | ${item.installedVersion} | ${item.license || "UNKNOWN"} | ${item.usedFor} |`,
    );
  }
  lines.push(
    "",
    "This file intentionally does not invent or rewrite upstream copyright statements.",
    "Use the resolved package contents referenced by the lockfile for authoritative upstream license files and notices.",
    "",
  );
  return `${lines.join("\n")}`;
};

export const loadCommittedInventory = () => readJson(inventoryPath);

const main = () => {
  const check = process.argv.includes("--check");
  const inventory = collectInventory();
  const inventoryText = normalizeInventory(inventory);
  const noticesText = renderNotices(inventory);
  if (check) {
    const committedInventory = readFileSync(inventoryPath, "utf8");
    const committedNotices = readFileSync(noticesPath, "utf8");
    if (committedInventory !== inventoryText || committedNotices !== noticesText) {
      throw new Error("third-party license inventory is stale; run this script without --check");
    }
    return;
  }
  writeFileSync(inventoryPath, inventoryText, "utf8");
  writeFileSync(noticesPath, noticesText, "utf8");
};

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
