import { getDom } from "./dom";
import { addMarkerFromSelection, showMarkers } from "./markers";
import { profileFor } from "./profiles";
import { renderInitialNow, renderMeta, renderNow, renderSpeakers } from "./render";
import { applySearch, jumpSearch, updateSearchControls } from "./search";
import { setStatus } from "./status";
import { findEvent, findSegment, renderTracks, scrollTimelineToTime, timelineHitAt, updatePlayhead, updateTimelineActive } from "./timeline";
import { estimateWordIndex, hydrateAllTurns, hydrateTurnWords, renderTranscript, scrollToTurn } from "./transcript";
import type { ActiveElements, AnyObject, ViewerState } from "./types";
import { debounce, seconds } from "./utils";

const dom = getDom();
const activeElements: ActiveElements = {
  turn: null,
  speaker: null,
  word: null,
};

const state: ViewerState = {
  timeline: {},
  profiles: [],
  profileById: new Map(),
  segments: [],
  events: [],
  markers: [],
  activeTurn: null,
  activeSpeaker: null,
  activeWord: null,
  selectedSpeaker: null,
  autoFollow: true,
  qaMode: false,
  wordOffset: 0,
  searchQuery: "",
  searchMatches: [],
  searchIndex: -1,
  segmentByTurn: new Map(),
  turnElementByTurn: new Map(),
  speakerElementById: new Map(),
  hydratedTurns: new Set(),
  timelineHitRegions: [],
  timelineCanvas: null,
  timelineActiveTurn: null,
  timelinePlayheadSeconds: 0,
};

function updateActiveWord(segment: AnyObject): void {
  hydrateTurnWords(state, activeElements.turn);
  const index = estimateWordIndex(segment, dom.audio.currentTime + state.wordOffset);
  const next = index === null ? null : `${segment.turn_index}:${index}`;
  if (next === state.activeWord) return;
  activeElements.word?.classList.remove("active");
  activeElements.word = null;
  state.activeWord = next;
  if (index === null) return;
  activeElements.word = dom.transcriptList.querySelector(
    `.word[data-turn="${CSS.escape(String(segment.turn_index))}"][data-word="${index}"]`,
  );
  activeElements.word?.classList.add("active");
}

function clearActiveWord(): void {
  activeElements.word?.classList.remove("active");
  activeElements.word = null;
  state.activeWord = null;
}

function refreshActiveClasses(): void {
  activeElements.turn?.classList.remove("active");
  activeElements.speaker?.classList.remove("active");
  activeElements.turn = null;
  activeElements.speaker = null;

  if (state.activeTurn && !state.activeTurn.startsWith("event:")) {
    activeElements.turn = state.turnElementByTurn.get(state.activeTurn) ?? null;
    activeElements.turn?.classList.add("active");
  }
  if (state.activeSpeaker) {
    activeElements.speaker = state.speakerElementById.get(state.activeSpeaker) ?? null;
    activeElements.speaker?.classList.add("active");
  }
  updateTimelineActive(state, state.activeTurn && !state.activeTurn.startsWith("event:") ? state.activeTurn : null);
}

function updateActive(forceScroll = false): void {
  updatePlayhead(dom, state);

  const event = findEvent(state, dom.audio.currentTime);
  if (event) {
    const key = `event:${event.id}`;
    if (state.activeTurn !== key) {
      state.activeTurn = key;
      state.activeSpeaker = null;
      renderNow(
        dom,
        {
          id: "non_speech",
          display_name: event.label || "No speech detected",
          title: "Pause / room audio",
          color: "#667085",
          initials: "NS",
        },
        undefined,
        event,
      );
      refreshActiveClasses();
      clearActiveWord();
    }
    return;
  }

  const segment = findSegment(state, dom.audio.currentTime);
  if (!segment) return;
  const changed = state.activeTurn !== String(segment.turn_index);
  state.activeTurn = String(segment.turn_index);
  state.activeSpeaker = segment.speaker;
  renderNow(dom, profileFor(state, segment.speaker), segment);
  if (changed) refreshActiveClasses();
  updateActiveWord(segment);
  if (forceScroll || changed) scrollTimelineToTime(dom, state, dom.audio.currentTime);
  if ((forceScroll || changed) && state.autoFollow) scrollToTurn(state, segment.turn_index);
}

function seekTo(time: number): void {
  dom.audio.currentTime = Math.max(0, Number(time || 0));
  updateActive(true);
}

function setSelectedSpeaker(speaker: string | null): void {
  state.selectedSpeaker = state.selectedSpeaker === speaker ? null : speaker;
  dom.speakerList.querySelectorAll<HTMLElement>(".speaker-card").forEach((card) => {
    card.classList.toggle("selected", Boolean(state.selectedSpeaker && card.dataset.speaker === state.selectedSpeaker));
  });
  applySearch(dom, state);
}

function loadTheme(): void {
  const saved = localStorage.getItem("scotus-theme");
  document.documentElement.dataset.theme = saved === "light" ? "light" : "dark";
}

function toggleTheme(): void {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("scotus-theme", next);
}

function bindEvents(): void {
  const debouncedSearch = debounce(() => applySearch(dom, state), 120);

  dom.audio.addEventListener("timeupdate", () => requestAnimationFrame(() => updateActive()));
  dom.audio.addEventListener("error", () => {
    setStatus(dom, "error", "Audio could not be loaded. Use the official audio link from the case data.");
  });
  dom.wordOffset.addEventListener("input", () => {
    state.wordOffset = Number(dom.wordOffset.value || 0);
    updateActiveWord(findSegment(state, dom.audio.currentTime) || {});
  });
  dom.autoFollow.addEventListener("change", () => {
    state.autoFollow = dom.autoFollow.checked;
  });
  dom.qaMode.addEventListener("change", () => {
    state.qaMode = dom.qaMode.checked;
    if (state.qaMode) hydrateAllTurns(state);
  });
  dom.search.addEventListener("input", debouncedSearch);
  dom.searchPrev.addEventListener("click", () => jumpSearch(dom, state, -1));
  dom.searchNext.addEventListener("click", () => jumpSearch(dom, state, 1));
  dom.themeToggle.addEventListener("click", toggleTheme);
  dom.exportMarkers.addEventListener("click", () => showMarkers(dom, state));

  dom.speakerList.addEventListener("click", (event) => {
    const button = (event.target as Element).closest<HTMLButtonElement>(".speaker-card");
    if (button) setSelectedSpeaker(button.dataset.speaker || null);
  });

  dom.tracks.addEventListener("click", (event) => {
    const hit = timelineHitAt(state, event.clientX, event.clientY);
    if (hit) seekTo(hit.time);
  });

  dom.transcriptList.addEventListener("click", (event) => {
    const timeButton = (event.target as Element).closest<HTMLButtonElement>("[data-time]");
    if (timeButton) seekTo(Number(timeButton.dataset.time || 0));
  });

  dom.transcriptList.addEventListener("mouseup", () => {
    window.setTimeout(() => addMarkerFromSelection(dom, state), 0);
  });

  window.addEventListener("keydown", (event) => {
    const target = event.target as HTMLElement | null;
    const interactive = target?.closest("input, textarea, select, button, a, [contenteditable='true']");
    if (event.code === "Slash" && !interactive) {
      event.preventDefault();
      dom.search.focus();
      return;
    }
    if (event.code !== "Space" || event.repeat || interactive) return;
    event.preventDefault();
    if (dom.audio.paused) dom.audio.play().catch(() => updateActive());
    else dom.audio.pause();
  });
}

async function fetchJson(url: string): Promise<AnyObject> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${url}`);
  return response.json();
}

async function init(): Promise<void> {
  loadTheme();
  setStatus(dom, "loading", "Loading case data...");
  const bundleUrl = dom.root.dataset.bundleUrl || "";
  const timelineUrl = dom.root.dataset.timelineUrl || "";
  const profilesUrl = dom.root.dataset.profilesUrl || "";

  let timeline: AnyObject;
  let profiles: AnyObject;
  let audioSrc = "";
  if (bundleUrl) {
    const bundle = await fetchJson(bundleUrl);
    timeline = bundle.timeline || {};
    profiles = bundle.profiles || {};
    audioSrc = typeof bundle.audio_src === "string" ? bundle.audio_src : "";
  } else {
    [timeline, profiles] = await Promise.all([fetchJson(timelineUrl), fetchJson(profilesUrl)]);
  }

  state.timeline = timeline;
  state.profiles = Array.isArray(profiles.profiles) ? profiles.profiles : [];
  state.profileById = new Map(state.profiles.map((profile) => [profile.id, profile]));
  state.segments = Array.isArray(timeline.segments)
    ? [...timeline.segments].sort((a, b) => Number(a.start || 0) - Number(b.start || 0))
    : [];
  state.events = Array.isArray(timeline.events) ? timeline.events.filter((event: AnyObject) => event.type === "non_speech") : [];
  state.segmentByTurn = new Map(state.segments.map((segment) => [String(segment.turn_index), segment]));
  state.autoFollow = dom.autoFollow.checked;
  state.qaMode = dom.qaMode.checked;
  state.wordOffset = Number(dom.wordOffset.value || 0);

  dom.audio.src = audioSrc || timeline.case?.audio_url || timeline.case?.audio_file || "";
  renderMeta(dom, state);
  renderSpeakers(dom, state);
  state.speakerElementById = new Map(
    [...dom.speakerList.querySelectorAll<HTMLElement>(".speaker-card")].map((speaker) => [String(speaker.dataset.speaker), speaker]),
  );
  renderTracks(dom, state);
  renderTranscript(dom, state);
  renderInitialNow(dom, state);
  bindEvents();
  applySearch(dom, state);
  updateSearchControls(dom, state);
  setStatus(dom, "ready");
  updateActive();
}

init().catch((error) => {
  console.error(error);
  dom.caseTitle.textContent = "Viewer failed to load";
  dom.caseMeta.textContent = error instanceof Error ? error.message : String(error);
  setStatus(dom, "error", error instanceof Error ? error.message : "Viewer failed to load.");
});
