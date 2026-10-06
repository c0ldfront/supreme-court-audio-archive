import { cp, mkdir, mkdtemp, readFile, rename, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

const viewerRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(viewerRoot, "..");

export function validateDocket(docket) {
  if (typeof docket !== "string" || !/^[0-9]+-[A-Za-z0-9-]+$/.test(docket)) {
    throw new Error("Provide a docket such as 24-624.");
  }
  return docket;
}

export function normalizeBase(base) {
  if (!base.startsWith("/") || base.startsWith("//") || /[?#\\\s]/.test(base) || base.split("/").includes("..")) {
    throw new Error("--base must be a URL path such as /court-transcripts/24-624/.");
  }
  return base.endsWith("/") ? base : `${base}/`;
}

// Stage on the destination filesystem so a failed copy leaves the current site intact.
export async function replaceSite(source, destination, { copy = cp, move = rename } = {}) {
  const stage = await mkdtemp(resolve(dirname(destination), ".site-publish-"));
  const next = resolve(stage, "next");
  const previous = resolve(stage, "previous");
  let backedUp = false;
  let preserveBackup = false;
  try {
    await copy(source, next, { recursive: true });
    try {
      await move(destination, previous);
      backedUp = true;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    try {
      await move(next, destination);
    } catch (error) {
      if (backedUp) {
        try {
          await move(previous, destination);
        } catch (rollbackError) {
          preserveBackup = true;
          throw new Error(`Site replacement and rollback failed; previous site saved at ${previous}.`,
            { cause: rollbackError });
        }
      }
      throw error;
    }
  } finally {
    if (!preserveBackup) await rm(stage, { recursive: true, force: true });
  }
}

export async function buildCase(docket, { base = "/", outputRoot = resolve(repoRoot, "output") } = {}) {
  validateDocket(docket);
  base = normalizeBase(base);
  const sourceDir = resolve(outputRoot, docket);
  const bundlePath = resolve(sourceDir, "bundle.json");
  const bundle = JSON.parse(await readFile(bundlePath, "utf-8"));
  if (bundle.schema_version !== "1" || bundle.case?.docket !== docket || bundle.audio_src !== "audio.mp3") {
    throw new Error("Bundle must have schema_version 1, the requested docket, and audio_src audio.mp3.");
  }
  const stagingRoot = resolve(viewerRoot, ".case-public");
  await mkdir(stagingRoot, { recursive: true });
  const stage = await mkdtemp(resolve(stagingRoot, "build-"));
  const publicDir = resolve(stage, "public");
  const siteDir = resolve(stage, "site");
  try {
    await mkdir(publicDir);
    await cp(bundlePath, resolve(publicDir, "bundle.json"));
    await cp(resolve(sourceDir, "audio.mp3"), resolve(publicDir, "audio.mp3"));
    const result = spawnSync(process.platform === "win32" ? "npm.cmd" : "npm", ["run", "build"], {
      cwd: viewerRoot,
      stdio: "inherit",
      env: {
        ...process.env,
        ASTRO_BASE: base,
        SCOTUS_CASE_DOCKET: docket,
        SCOTUS_CASE_TITLE: bundle.case?.name || `Case ${docket}`,
        SCOTUS_OUT_DIR: siteDir,
        SCOTUS_PUBLIC_DIR: publicDir,
      },
    });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`Viewer build failed (exit ${result.status}).`);
    const destination = resolve(sourceDir, "site");
    await replaceSite(siteDir, destination);
    return destination;
  } finally {
    await rm(stage, { recursive: true, force: true });
  }
}

async function main() {
  const [docket, ...args] = process.argv.slice(2);
  if (args.length && (args.length !== 2 || args[0] !== "--base")) {
    throw new Error("Usage: npm run build:case -- <docket> [--base /repository/docket/]");
  }
  const destination = await buildCase(docket, { base: args[1] || process.env.ASTRO_BASE || "/" });
  console.log(`Built static viewer: ${destination}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
