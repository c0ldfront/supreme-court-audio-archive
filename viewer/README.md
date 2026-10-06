# SCOTUS Viewer

Astro frontend for the pipeline's versioned case bundles. Requires Node.js 22.12+
with npm. Run commands from this directory.

```bash
npm ci
npm run typecheck
npm test
npm run build
```

The default build is an empty landing page. There are no embedded case fixtures,
recordings, or hard-coded links to historical demo cases.

## Package a case

```bash
npm run build:case -- 24-624
npm run build:case -- 24-624 --base /court-transcripts/24-624/
```

Input: `../output/<docket>/bundle.json` and `audio.mp3`.
Output: `../output/<docket>/site/`, containing HTML, browser assets, the bundle,
and audio. The base defaults to `/`; `ASTRO_BASE` supplies an alternative default.
`ASTRO_SITE` optionally supplies the deployment origin to Astro.

The packager checks docket and bundle identity before building. It stages inputs
in a temporary directory, builds there, and replaces the generated `site/` only
after Astro succeeds. Input bundles and recordings remain intact on build errors.
Serve the generated folder over HTTP; opening HTML with `file://` cannot reliably
fetch the bundle. Publication is a separate operation.

`npm run dev` and `npm run preview` serve the generic landing page by default.
Use a packaged case with a local static server to inspect real case data.

## Features

Audio playback, speaker selection, transcript search, timeline seeking,
word-level highlighting with adjustable offset, theme switching, and QA marker
export. Alignment quality depends on the pipeline output; the viewer does not
verify transcript timing accuracy.
