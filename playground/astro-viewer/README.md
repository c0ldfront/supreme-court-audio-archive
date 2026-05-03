# SCOTUS Astro Viewer Playground

This is an isolated Astro static-site playground for the Supreme Court case viewer. It does not replace or modify the working Python pipeline.

## Commands

```bash
npm install
npm run dev
npm run typecheck
npm run build
```

For GitHub Pages project sites, set the repository base path before building:

```bash
ASTRO_BASE=/your-repo-name/ npm run build
```

The convenience script defaults to `/supreme-court-ingest/`:

```bash
npm run build:github
```

## Data Layout

```text
public/data/cases/2025/24-109/
  timeline.json
  profiles.json
```

The viewer loads these JSON files at runtime. The MP3 is not copied into this playground; the page uses the official SCOTUS `audio_url` from the timeline data.

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
