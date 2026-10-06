# Release review

This repository contains source, configuration examples, tests, and CI. Court
recordings, transcripts, model caches, generated sites, and local backups are
excluded from the current tree. Git history has been curated separately from
local runtime data; history backup bundles must not be published.

Before publishing:

- Choose a repository name and a license for the project's original code. No
  license has been selected on the owner's behalf.
- Review source-data and third-party asset terms separately from the code
  license, especially externally referenced speaker photographs.
- Run the checks in `CONTRIBUTING.md`. CI builds the wheel, verifies installed
  config generation, and builds the viewer without private local data.
- Review real-case alignment warnings. Offline checks do not exercise a fresh
  WhisperX model inference or guarantee timing quality.
- Set the viewer's deployment base explicitly if publishing generated case sites.

There is no automatic publishing workflow. CI only tests and builds.
