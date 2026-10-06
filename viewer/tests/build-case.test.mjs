import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtemp, mkdir, readFile, writeFile, rename, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { buildCase, replaceSite, normalizeBase, validateDocket } from "../scripts/build-case.mjs";

test("docket and base cannot escape the case directory", () => {
  assert.equal(validateDocket("24-624"), "24-624");
  for (const docket of [undefined, "../case", "24-624/../../", "/tmp", ""]) {
    assert.throws(() => validateDocket(docket));
  }
  assert.equal(normalizeBase("/court/24-624"), "/court/24-624/");
  for (const base of ["https://host/", "//host/", "/../", "/a?b", "/a\\b"]) {
    assert.throws(() => normalizeBase(base));
  }
});

test("case packaging builds a portable site and preserves source artifacts on failure", async () => {
  const outputRoot = await mkdtemp(resolve(tmpdir(), "scotus-build-test-"));
  const source = resolve(outputRoot, "24-624");
  await mkdir(source);
  const bundle = JSON.stringify({schema_version: "1", case: {docket: "24-624", name: "Example case"},
    audio_src: "audio.mp3", timeline: {segments: []}, profiles: {profiles: []}});
  await writeFile(resolve(source, "bundle.json"), bundle);
  await writeFile(resolve(source, "audio.mp3"), "test audio");
  try {
    const site = await buildCase("24-624", {outputRoot, base: "/court/24-624/"});
    const html = await readFile(resolve(site, "index.html"), "utf8");
    assert.match(html, /Example case/);
    assert.match(html, /data-bundle-url="\/court\/24-624\/bundle.json"/);
    assert.equal(await readFile(resolve(site, "bundle.json"), "utf8"), bundle);
    assert.equal(await readFile(resolve(source, "bundle.json"), "utf8"), bundle);
    await rm(resolve(source, "audio.mp3"));
    await assert.rejects(buildCase("24-624", {outputRoot}));
    assert.equal(await readFile(resolve(site, "index.html"), "utf8"), html);
  } finally {
    await rm(outputRoot, {recursive: true, force: true});
  }
});


test("site replacement preserves the previous build after copy and rename failures", async () => {
  const root = await mkdtemp(resolve(tmpdir(), "scotus-publish-test-"));
  const source = resolve(root, "source");
  const destination = resolve(root, "site");
  await mkdir(source);
  await mkdir(destination);
  await writeFile(resolve(source, "index.html"), "new site");
  await writeFile(resolve(destination, "index.html"), "previous site");
  try {
    await assert.rejects(replaceSite(source, destination, {
      copy: async () => { throw new Error("disk full"); },
    }), /disk full/);
    assert.equal(await readFile(resolve(destination, "index.html"), "utf8"), "previous site");
    await assert.rejects(replaceSite(source, destination, {
      move: async (from, to) => {
        if (from.endsWith("/next")) throw new Error("rename failed");
        return rename(from, to);
      },
    }), /rename failed/);
    assert.equal(await readFile(resolve(destination, "index.html"), "utf8"), "previous site");
    await replaceSite(source, destination);
    assert.equal(await readFile(resolve(destination, "index.html"), "utf8"), "new site");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
