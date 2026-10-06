import type { ViewerDom } from "./types";

function requiredElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing required viewer element: #${id}`);
  return element as T;
}

export function getDom(): ViewerDom {
  return {
    root: requiredElement("viewer-root"),
    audio: requiredElement("audio"),
    caseTitle: requiredElement("case-title"),
    caseMeta: requiredElement("case-meta"),
    wordOffset: requiredElement("word-offset"),
    autoFollow: requiredElement("auto-follow"),
    qaMode: requiredElement("qa-mode"),
    nowCard: requiredElement("now-card"),
    speakerList: requiredElement("speaker-list"),
    tracks: requiredElement("tracks"),
    transcriptList: requiredElement("transcript-list"),
    timelineSummary: requiredElement("timeline-summary"),
    search: requiredElement("search"),
    searchCount: requiredElement("search-count"),
    searchPrev: requiredElement("search-prev"),
    searchNext: requiredElement("search-next"),
    themeToggle: requiredElement("theme-toggle"),
    exportMarkers: requiredElement("export-markers"),
    qaDialog: requiredElement("qa-dialog"),
    qaJson: requiredElement("qa-json"),
    status: requiredElement("viewer-status"),
    statusText: requiredElement("viewer-status-text"),
  };
}
