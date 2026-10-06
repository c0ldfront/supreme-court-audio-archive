# SCOTUS Astro Viewer Playground

This is an isolated Astro static-site playground for the Supreme Court case viewer. It does not replace or modify the working Python pipeline.

## Commands

```bash
npm install
npm run dev
npm run typecheck
npm run build
```

Build a GitHub Pages-ready static viewer for a v2 pipeline output:

```bash
npm run build:case -- 24-624
```

That command reads `../../output/24-624/{bundle.json,audio.mp3}`, stages those
assets for Astro, and writes the packaged static viewer back to
`../../output/24-624/`.

For GitHub Pages project sites, set the repository base path before building:

```bash
ASTRO_BASE=/your-repo-name/ npm run build
```

For a packaged case folder, include the docket in the base path:

```bash
npm run build:case -- 24-624 --base /supreme-court-ingest/24-624/
```

The convenience script defaults to `/supreme-court-ingest/`:

```bash
npm run build:github
```

## Data Layout

```text
../../output/<docket>/
  index.html
  _astro/
  bundle.json
  audio.mp3
```

The packaged viewer loads `bundle.json` at runtime. The bundle contains the
timeline and speaker profiles, and its `audio_src` points to the adjacent
`audio.mp3`.

## Current Scope

- Static Astro archive index.
- Static Astro case route at `/cases/2025/24-109/`.
- Native audio playback.
- Argument timeline click-to-seek.
- Speaker list and active speaker portrait.
- Transcript turn following.
- Word-level highlighting with configurable offset.
- Spacebar play/pause.
- Light/dark theme.
- QA marker selection and JSON export.
