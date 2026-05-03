type AnyObject = Record<string, any>;

type ViewerState = {
  timeline: AnyObject;
  profiles: AnyObject[];
  profileById: Map<string, AnyObject>;
  segments: AnyObject[];
  events: AnyObject[];
  markers: AnyObject[];
  activeTurn: string | null;
  activeSpeaker: string | null;
  activeWord: string | null;
  selectedSpeaker: string | null;
  autoFollow: boolean;
  qaMode: boolean;
  wordOffset: number;
};

const root = document.getElementById("viewer-root") as HTMLElement;
const dom = {
  audio: document.getElementById("audio") as HTMLAudioElement,
  caseTitle: document.getElementById("case-title") as HTMLElement,
  caseMeta: document.getElementById("case-meta") as HTMLElement,
  currentTime: document.getElementById("current-time") as HTMLElement,
  duration: document.getElementById("duration") as HTMLElement,
  seek: document.getElementById("seek") as HTMLInputElement,
  wordOffset: document.getElementById("word-offset") as HTMLInputElement,
  autoFollow: document.getElementById("auto-follow") as HTMLInputElement,
  qaMode: document.getElementById("qa-mode") as HTMLInputElement,
  nowCard: document.getElementById("now-card") as HTMLElement,
  speakerList: document.getElementById("speaker-list") as HTMLElement,
  tracks: document.getElementById("tracks") as HTMLElement,
  transcriptList: document.getElementById("transcript-list") as HTMLElement,
  timelineSummary: document.getElementById("timeline-summary") as HTMLElement,
  search: document.getElementById("search") as HTMLInputElement,
  themeToggle: document.getElementById("theme-toggle") as HTMLButtonElement,
  exportMarkers: document.getElementById("export-markers") as HTMLButtonElement,
  qaDialog: document.getElementById("qa-dialog") as HTMLDialogElement,
  qaJson: document.getElementById("qa-json") as HTMLTextAreaElement,
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
};

function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const chars: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return chars[char] ?? char;
  });
}

function seconds(value: number | string | null | undefined, precise = false): string {
  const total = Math.max(0, Number(value || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const wholeSeconds = Math.floor(total % 60);
  const fraction = precise ? `.${String(Math.floor((total % 1) * 100)).padStart(2, "0")}` : "";
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}${fraction}`;
  return `${minutes}:${String(wholeSeconds).padStart(2, "0")}${fraction}`;
}

function profileFor(id: string | null | undefined): AnyObject {
  if (id && state.profileById.has(id)) return state.profileById.get(id)!;
  const label = id || "Unknown";
  return {
    id: label,
    display_name: label,
    short_name: label,
    title: "Speaker",
    role: "speaker",
    initials: label
      .split(/\s+/)
      .filter(Boolean)
      .slice(-2)
      .map((part) => part[0])
      .join(""),
    color: "#667085",
  };
}

function avatar(profile: AnyObject, className = "avatar"): string {
  const color = escapeHtml(profile.color || "#667085");
  if (profile.photo_url) {
    return `<span class="${className}" style="background:${color}"><img src="${escapeHtml(profile.photo_url)}" alt="${escapeHtml(profile.display_name || profile.id)}" loading="lazy"></span>`;
  }
  return `<span class="${className}" style="background:${color}">${escapeHtml(profile.initials || "?")}</span>`;
}

function renderTimedText(segment: AnyObject): string {
  const parts = String(segment.text || "").split(/(\s+)/);
  const textWordCount = parts.filter((part) => part.trim()).length;
  const alignedWords = Array.isArray(segment.words) ? segment.words : [];
  const start = Number(segment.start || 0);
  const end = Number(segment.end || start);
  const duration = Math.max(0.01, end - start);
  let wordIndex = 0;

  return parts
    .map((part) => {
      if (!part.trim()) return escapeHtml(part);
      const aligned = alignedWords[wordIndex] || {};
      const fallbackStart = start + duration * (wordIndex / Math.max(1, textWordCount));
      const fallbackEnd = start + duration * ((wordIndex + 1) / Math.max(1, textWordCount));
      const wordStart = Number.isFinite(Number(aligned.start)) ? Number(aligned.start) : fallbackStart;
      const wordEnd = Number.isFinite(Number(aligned.end)) ? Number(aligned.end) : fallbackEnd;
      const marked = isWordMarked(segment.turn_index, wordIndex) ? " qa-marked" : "";
      const html = `<span class="word${marked}" data-turn="${escapeHtml(segment.turn_index)}" data-word="${wordIndex}" data-start="${wordStart.toFixed(3)}" data-end="${wordEnd.toFixed(3)}">${escapeHtml(part)}</span>`;
      wordIndex += 1;
      return html;
    })
    .join("");
}

function isWordMarked(turnIndex: unknown, wordIndex: number): boolean {
  return state.markers.some((marker) => {
    const selection = marker.selection || {};
    return (
      String(marker.turn?.turn_index) === String(turnIndex) &&
      wordIndex >= Number(selection.word_start_index) &&
      wordIndex <= Number(selection.word_end_index)
    );
  });
}

function renderMeta(): void {
  const c = state.timeline.case || {};
  dom.caseTitle.textContent = c.name || "Untitled case";
  dom.caseMeta.innerHTML = [
    c.docket,
    c.date_argued_iso || c.date_argued,
    c.term ? `${c.term} term` : null,
    state.timeline.duration ? `${Math.round(Number(state.timeline.duration) / 60)} min` : null,
    `${state.segments.length} turns`,
  ]
    .filter(Boolean)
    .map((item) => `<span>${escapeHtml(item)}</span>`)
    .join("");
  dom.timelineSummary.textContent = `${state.segments.length} turns · ${state.events.length} no-speech gaps`;
  dom.duration.textContent = seconds(Number(state.timeline.duration || 0));
  dom.seek.max = String(Number(state.timeline.duration || 0));
}

function renderSpeakers(): void {
  dom.speakerList.innerHTML = state.profiles
    .map((profile) => {
      const stats = profile.stats || {};
      return `<button class="speaker-card" type="button" data-speaker="${escapeHtml(profile.id)}">
        ${avatar(profile, "speaker-thumb")}
        <span>
          <span class="speaker-name">${escapeHtml(profile.short_name || profile.display_name || profile.id)}</span>
          <span class="speaker-role">${escapeHtml(profile.title || profile.role || "Speaker")}</span>
        </span>
        <span class="speaker-count">${Number(stats.turn_count || 0)}</span>
      </button>`;
    })
    .join("");
}

function renderTracks(): void {
  const duration = Math.max(1, Number(state.timeline.duration || 1));
  const axisHeight = 24;
  const laneHeight = 23;
  const laneMap = new Map(state.profiles.map((profile, index) => [profile.id, index]));
  const laneCount = Math.max(1, state.profiles.length);
  const fallbackLane = laneCount - 1;
  const labels = state.profiles
    .map((profile) => {
      return `<div class="lane-label" style="--speaker-color:${escapeHtml(profile.color || "#667085")}">${escapeHtml(profile.short_name || profile.display_name || profile.id)}</div>`;
    })
    .join("");
  const axisTicks = Array.from({ length: 11 }, (_, index) => {
    const left = index * 10;
    return `<div class="axis-tick" style="left:${left}%"><span>${seconds(duration * (left / 100))}</span></div>`;
  }).join("");
  const turnBars = state.segments
    .map((segment) => {
      const profile = profileFor(segment.speaker);
      const left = (Number(segment.start || 0) / duration) * 100;
      const width = Math.max(0.05, ((Number(segment.end || segment.start || 0) - Number(segment.start || 0)) / duration) * 100);
      const lane = laneMap.get(segment.speaker) ?? fallbackLane;
      const top = axisHeight + lane * laneHeight + 6;
      return `<button class="segment-bar" type="button" title="${escapeHtml(profile.display_name)} ${seconds(segment.start)} - ${seconds(segment.end)}" aria-label="${escapeHtml(profile.display_name)} ${seconds(segment.start)} to ${seconds(segment.end)}" data-turn="${escapeHtml(segment.turn_index)}" data-time="${Number(segment.start || 0)}" style="left:${left}%;width:${width}%;top:${top}px;--speaker-color:${escapeHtml(profile.color || "#667085")}"></button>`;
    })
    .join("");
  const eventBars = state.events
    .map((event) => {
      const left = (Number(event.start || 0) / duration) * 100;
      const width = Math.max(0.05, ((Number(event.end || event.start || 0) - Number(event.start || 0)) / duration) * 100);
      const top = axisHeight + laneCount * laneHeight + 6;
      return `<button class="event-bar" type="button" title="${escapeHtml(event.label || "No speech")} ${seconds(event.start)} - ${seconds(event.end)}" aria-label="${escapeHtml(event.label || "No speech")} ${seconds(event.start)} to ${seconds(event.end)}" data-time="${Number(event.start || 0)}" style="left:${left}%;width:${width}%;top:${top}px"></button>`;
    })
    .join("");
  dom.tracks.innerHTML = `
    <div class="timeline-labels">
      ${labels}
      <div class="lane-label event-label">No speech</div>
    </div>
    <div class="timeline-scroll">
      <div class="timeline-canvas" style="--lane-count:${laneCount}">
        <div class="timeline-axis">${axisTicks}</div>
        <div class="playhead" style="left:0%"></div>
        ${turnBars}
        ${eventBars}
      </div>
    </div>`;
}

function renderTranscript(): void {
  const html = state.segments
    .map((segment) => {
      const profile = profileFor(segment.speaker);
      return `<article class="turn" data-turn="${escapeHtml(segment.turn_index)}" data-speaker="${escapeHtml(segment.speaker)}" data-text="${escapeHtml(segment.text || "")}">
        <header class="turn-head">
          <span class="turn-speaker" style="color:${escapeHtml(profile.color || "currentColor")}">${escapeHtml(profile.display_name || segment.speaker)}</span>
          <button class="turn-time text-button" type="button" data-time="${Number(segment.start || 0)}">${seconds(segment.start)} - ${seconds(segment.end)}</button>
        </header>
        <div class="turn-text">${renderTimedText(segment)}</div>
      </article>`;
    })
    .join("");
  dom.transcriptList.innerHTML = html;
}

function renderNow(profile: AnyObject, segment?: AnyObject, event?: AnyObject): void {
  dom.nowCard.innerHTML = `${avatar(profile)}
    <div>
      <div class="now-name">${escapeHtml(profile.display_name || profile.id)}</div>
      <div class="now-title">${escapeHtml(profile.title || profile.role || "")}</div>
      <div class="now-time">${event ? `${seconds(event.start)} - ${seconds(event.end)}` : segment ? `${seconds(segment.start)} - ${seconds(segment.end)}` : seconds(dom.audio.currentTime)}</div>
    </div>`;
}

function findEvent(time: number): AnyObject | null {
  return state.events.find((event) => time >= Number(event.start || 0) && time < Number(event.end || event.start || 0)) || null;
}

function findSegment(time: number): AnyObject | null {
  let low = 0;
  let high = state.segments.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const segment = state.segments[mid];
    const start = Number(segment.start || 0);
    const end = Number(segment.end || start);
    if (time < start) high = mid - 1;
    else if (time >= end) low = mid + 1;
    else return segment;
  }
  return null;
}

function estimateWordIndex(segment: AnyObject, time: number): number | null {
  const textWords = String(segment.text || "").match(/\S+/g) || [];
  if (!textWords.length) return null;
  const aligned = Array.isArray(segment.words) ? segment.words : [];
  if (aligned.length) {
    let nearest: number | null = null;
    for (let index = 0; index < aligned.length; index += 1) {
      const start = Number(aligned[index].start);
      const end = Number(aligned[index].end);
      if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
      if (time >= start && time < end) return index;
      if (time >= start) nearest = index;
    }
    return nearest;
  }
  const start = Number(segment.start || 0);
  const end = Number(segment.end || start);
  const ratio = Math.max(0, Math.min(0.999, (time - start) / Math.max(0.01, end - start)));
  return Math.floor(ratio * textWords.length);
}

function updateActiveWord(segment: AnyObject): void {
  const index = estimateWordIndex(segment, dom.audio.currentTime + state.wordOffset);
  const next = index === null ? null : `${segment.turn_index}:${index}`;
  if (next === state.activeWord) return;
  dom.transcriptList.querySelectorAll(".word.active").forEach((node) => node.classList.remove("active"));
  state.activeWord = next;
  if (index === null) return;
  dom.transcriptList
    .querySelector(`.word[data-turn="${CSS.escape(String(segment.turn_index))}"][data-word="${index}"]`)
    ?.classList.add("active");
}

function scrollToTurn(turnIndex: unknown): void {
  dom.transcriptList
    .querySelector(`.turn[data-turn="${CSS.escape(String(turnIndex))}"]`)
    ?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function refreshActiveClasses(): void {
  dom.transcriptList.querySelectorAll(".turn.active").forEach((node) => node.classList.remove("active"));
  dom.speakerList.querySelectorAll(".speaker-card.active").forEach((node) => node.classList.remove("active"));
  dom.tracks.querySelectorAll(".segment-bar.active").forEach((node) => node.classList.remove("active"));
  if (state.activeTurn) {
    dom.transcriptList.querySelector(`.turn[data-turn="${CSS.escape(state.activeTurn)}"]`)?.classList.add("active");
    dom.tracks.querySelector(`.segment-bar[data-turn="${CSS.escape(state.activeTurn)}"]`)?.classList.add("active");
  }
  if (state.activeSpeaker) {
    dom.speakerList.querySelector(`.speaker-card[data-speaker="${CSS.escape(state.activeSpeaker)}"]`)?.classList.add("active");
  }
}

function updateActive(forceScroll = false): void {
  dom.currentTime.textContent = seconds(dom.audio.currentTime);
  if (!dom.seek.matches(":active")) dom.seek.value = String(dom.audio.currentTime);
  updatePlayhead();

  const event = findEvent(dom.audio.currentTime);
  if (event) {
    const key = `event:${event.id}`;
    if (state.activeTurn !== key) {
      state.activeTurn = key;
      state.activeSpeaker = null;
      state.activeWord = null;
      renderNow(
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
      dom.transcriptList.querySelectorAll(".word.active").forEach((node) => node.classList.remove("active"));
    }
    return;
  }

  const segment = findSegment(dom.audio.currentTime);
  if (!segment) return;
  const changed = state.activeTurn !== String(segment.turn_index);
  state.activeTurn = String(segment.turn_index);
  state.activeSpeaker = segment.speaker;
  renderNow(profileFor(segment.speaker), segment);
  refreshActiveClasses();
  updateActiveWord(segment);
  if (forceScroll || changed) scrollTimelineToTime(dom.audio.currentTime);
  if ((forceScroll || changed) && state.autoFollow) scrollToTurn(segment.turn_index);
}

function updatePlayhead(): void {
  const duration = Number(dom.audio.duration || state.timeline.duration || 0);
  if (!Number.isFinite(duration) || duration <= 0) return;
  const left = Math.max(0, Math.min(100, (dom.audio.currentTime / duration) * 100));
  const playhead = dom.tracks.querySelector<HTMLElement>(".playhead");
  if (playhead) playhead.style.left = `${left}%`;
}

function scrollTimelineToTime(time: number): void {
  const duration = Number(dom.audio.duration || state.timeline.duration || 0);
  const scroller = dom.tracks.querySelector<HTMLElement>(".timeline-scroll");
  const canvas = dom.tracks.querySelector<HTMLElement>(".timeline-canvas");
  if (!scroller || !canvas || !Number.isFinite(duration) || duration <= 0) return;
  const x = (Math.max(0, Math.min(duration, time)) / duration) * canvas.scrollWidth;
  scroller.scrollTo({
    left: Math.max(0, x - scroller.clientWidth * 0.5),
    behavior: "smooth",
  });
}

function seekTo(time: number): void {
  dom.audio.currentTime = Math.max(0, Number(time || 0));
  updateActive(true);
}

function applyFilters(): void {
  const query = dom.search.value.trim().toLowerCase();
  const speaker = state.selectedSpeaker;
  dom.transcriptList.querySelectorAll<HTMLElement>(".turn").forEach((turn) => {
    const matchesSpeaker = !speaker || turn.dataset.speaker === speaker;
    const matchesText = !query || String(turn.dataset.text || "").toLowerCase().includes(query);
    turn.classList.toggle("hidden", !matchesSpeaker || !matchesText);
  });
}

function selectedWords(): HTMLElement[] {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return [];
  const range = selection.getRangeAt(0);
  const turn = (range.commonAncestorContainer instanceof Element
    ? range.commonAncestorContainer
    : range.commonAncestorContainer.parentElement
  )?.closest(".turn");
  if (!turn) return [];
  return [...turn.querySelectorAll<HTMLElement>(".word")].filter((word) => range.intersectsNode(word));
}

function addMarkerFromSelection(): void {
  if (!state.qaMode) return;
  const words = selectedWords();
  if (!words.length) return;
  const first = words[0];
  const last = words[words.length - 1];
  const turnIndex = first.dataset.turn;
  const segment = state.segments.find((item) => String(item.turn_index) === String(turnIndex));
  if (!segment) return;
  const phrase = words.map((word) => word.textContent || "").join(" ").replace(/\s+/g, " ").trim();
  const marker = {
    id: `qa-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    created_at: new Date().toISOString(),
    expected_time_seconds: Number(dom.audio.currentTime.toFixed(3)),
    expected_time: seconds(dom.audio.currentTime, true),
    phrase,
    note: "",
    case: {
      docket: state.timeline.case?.docket,
      name: state.timeline.case?.name,
      date_argued_iso: state.timeline.case?.date_argued_iso,
    },
    turn: {
      turn_index: segment.turn_index,
      speaker: segment.speaker,
      start_seconds: segment.start,
      end_seconds: segment.end,
      timing: segment.source?.timing,
      word_alignment_status: segment.source?.word_alignment_status ?? null,
      word_alignment_mean_score: segment.source?.word_alignment_mean_score ?? null,
    },
    selection: {
      word_start_index: Number(first.dataset.word),
      word_end_index: Number(last.dataset.word),
      word_count: words.length,
      word_start_seconds: Number(first.dataset.start),
      word_end_seconds: Number(last.dataset.end),
    },
  };
  state.markers.push(marker);
  words.forEach((word) => word.classList.add("qa-marked"));
}

function markerPayload(): AnyObject {
  const c = state.timeline.case || {};
  return {
    schema: "scotus_alignment_qa_markers.v1",
    created_at: new Date().toISOString(),
    case: {
      docket: c.docket,
      name: c.name,
      term: c.term,
      date_argued_iso: c.date_argued_iso,
      audio_file: c.audio_file,
      audio_url: c.audio_url,
    },
    timeline: {
      duration_seconds: state.timeline.duration,
      timing_method: state.timeline.timing?.method,
      timing_confidence: state.timeline.timing?.confidence,
    },
    markers: state.markers,
  };
}

function showMarkers(): void {
  dom.qaJson.value = JSON.stringify(markerPayload(), null, 2);
  dom.qaDialog.showModal();
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
  dom.audio.addEventListener("timeupdate", () => requestAnimationFrame(() => updateActive()));
  dom.audio.addEventListener("loadedmetadata", () => {
    dom.seek.max = String(dom.audio.duration || state.timeline.duration || 0);
    dom.duration.textContent = seconds(dom.audio.duration || state.timeline.duration || 0);
  });
  dom.seek.addEventListener("input", () => seekTo(Number(dom.seek.value)));
  dom.wordOffset.addEventListener("input", () => {
    state.wordOffset = Number(dom.wordOffset.value || 0);
    updateActiveWord(findSegment(dom.audio.currentTime) || {});
  });
  dom.autoFollow.addEventListener("change", () => {
    state.autoFollow = dom.autoFollow.checked;
  });
  dom.qaMode.addEventListener("change", () => {
    state.qaMode = dom.qaMode.checked;
  });
  dom.search.addEventListener("input", applyFilters);
  dom.themeToggle.addEventListener("click", toggleTheme);
  dom.exportMarkers.addEventListener("click", showMarkers);

  dom.speakerList.addEventListener("click", (event) => {
    const button = (event.target as Element).closest<HTMLButtonElement>(".speaker-card");
    if (!button) return;
    state.selectedSpeaker = state.selectedSpeaker === button.dataset.speaker ? null : button.dataset.speaker || null;
    dom.speakerList.querySelectorAll(".speaker-card").forEach((card) => {
      card.classList.toggle("active", Boolean(state.selectedSpeaker && (card as HTMLElement).dataset.speaker === state.selectedSpeaker));
    });
    applyFilters();
  });

  dom.tracks.addEventListener("click", (event) => {
    const button = (event.target as Element).closest<HTMLButtonElement>("[data-time]");
    if (button) seekTo(Number(button.dataset.time || 0));
  });

  dom.transcriptList.addEventListener("click", (event) => {
    const timeButton = (event.target as Element).closest<HTMLButtonElement>("[data-time]");
    if (timeButton) seekTo(Number(timeButton.dataset.time || 0));
  });

  dom.transcriptList.addEventListener("mouseup", () => {
    window.setTimeout(addMarkerFromSelection, 0);
  });

  window.addEventListener("keydown", (event) => {
    const target = event.target as HTMLElement | null;
    const interactive = target?.closest("input, textarea, select, button, a, [contenteditable='true']");
    if (event.code !== "Space" || event.repeat || interactive) return;
    event.preventDefault();
    if (dom.audio.paused) dom.audio.play().catch(() => updateActive());
    else dom.audio.pause();
  });
}

async function init(): Promise<void> {
  loadTheme();
  const timelineUrl = root.dataset.timelineUrl || "";
  const profilesUrl = root.dataset.profilesUrl || "";
  const [timeline, profiles] = await Promise.all([
    fetch(timelineUrl).then((response) => response.json()),
    fetch(profilesUrl).then((response) => response.json()),
  ]);
  state.timeline = timeline;
  state.profiles = Array.isArray(profiles.profiles) ? profiles.profiles : [];
  state.profileById = new Map(state.profiles.map((profile) => [profile.id, profile]));
  state.segments = Array.isArray(timeline.segments)
    ? [...timeline.segments].sort((a, b) => Number(a.start || 0) - Number(b.start || 0))
    : [];
  state.events = Array.isArray(timeline.events) ? timeline.events.filter((event: AnyObject) => event.type === "non_speech") : [];
  state.autoFollow = dom.autoFollow.checked;
  state.qaMode = dom.qaMode.checked;
  state.wordOffset = Number(dom.wordOffset.value || 0);

  dom.audio.src = timeline.case?.audio_url || timeline.case?.audio_file || "";
  renderMeta();
  renderSpeakers();
  renderTracks();
  renderTranscript();
  renderNow(profileFor(state.segments[0]?.speaker), state.segments[0]);
  bindEvents();
  updateActive();
}

init().catch((error) => {
  console.error(error);
  dom.caseTitle.textContent = "Viewer failed to load";
  dom.caseMeta.textContent = error instanceof Error ? error.message : String(error);
});
