import { cp, mkdir, readFile, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const viewerRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(viewerRoot, "..");

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index === -1 ? undefined : process.argv[index + 1];
}

const docket = process.argv[2] && !process.argv[2].startsWith("--") ? process.argv[2] : process.env.SCOTUS_CASE_DOCKET;
if (!docket) {
  console.error("Usage: npm run build:case -- <docket> [--base /repo/<docket>/]");
  process.exit(2);
}

const sourceDir = resolve(repoRoot, "output", docket);
const stagePublicDir = resolve(viewerRoot, ".case-public", docket);
const outDir = sourceDir;
const base = argValue("--base") || process.env.ASTRO_BASE || `/supreme-court-ingest/${docket}/`;

const bundlePath = resolve(sourceDir, "bundle.json");
const audioPath = resolve(sourceDir, "audio.mp3");
const bundle = JSON.parse(await readFile(bundlePath, "utf-8"));
const caseTitle = bundle.case?.name || `Case ${docket}`;

await rm(stagePublicDir, { recursive: true, force: true });
await mkdir(stagePublicDir, { recursive: true });
await cp(bundlePath, resolve(stagePublicDir, "bundle.json"));
await cp(audioPath, resolve(stagePublicDir, "audio.mp3"));

const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const result = spawnSync(npm, ["run", "build"], {
  cwd: viewerRoot,
  stdio: "inherit",
  env: {
    ...process.env,
    ASTRO_BASE: base,
    SCOTUS_CASE_DOCKET: docket,
    SCOTUS_CASE_TITLE: caseTitle,
    SCOTUS_OUT_DIR: outDir,
    SCOTUS_PUBLIC_DIR: stagePublicDir,
  },
});

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

await rm(resolve(outDir, "cases"), { recursive: true, force: true });

console.log(`Built static viewer for ${docket}: ${outDir}`);
