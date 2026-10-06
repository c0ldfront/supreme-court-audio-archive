import type { ViewerDom, ViewerState } from "./types";
import { hydrateTurnWords } from "./transcript";
import { normalizeSearch } from "./utils";

export function applySearch(dom: ViewerDom, state: ViewerState): void {
  const query = normalizeSearch(dom.search.value);
  const speaker = state.selectedSpeaker;
  state.searchQuery = query;
  state.searchMatches = [];
  state.searchIndex = -1;

  dom.transcriptList.querySelectorAll<HTMLElement>(".turn").forEach((turn) => {
    turn.classList.remove("search-match", "search-current");
    turn.querySelectorAll(".word.search-hit").forEach((word) => word.classList.remove("search-hit"));

    const matchesSpeaker = !speaker || turn.dataset.speaker === speaker;
    const matchesText = !query || normalizeSearch(turn.dataset.text).includes(query);
    const visible = matchesSpeaker && matchesText;
    turn.classList.toggle("hidden", !visible);

    if (query && visible) {
      hydrateTurnWords(state, turn);
      turn.classList.add("search-match");
      state.searchMatches.push(turn);
      markSearchWords(turn, query);
    }
  });

  if (state.searchMatches.length) {
    state.searchIndex = 0;
    setCurrentSearchResult(dom, state, 0, false);
  }
  updateSearchControls(dom, state);
}

export function jumpSearch(dom: ViewerDom, state: ViewerState, direction: 1 | -1): void {
  if (!state.searchMatches.length) return;
  const next = (state.searchIndex + direction + state.searchMatches.length) % state.searchMatches.length;
  setCurrentSearchResult(dom, state, next, true);
  updateSearchControls(dom, state);
}

export function updateSearchControls(dom: ViewerDom, state: ViewerState): void {
  if (!state.searchQuery) {
    dom.searchCount.textContent = "";
  } else if (!state.searchMatches.length) {
    dom.searchCount.textContent = "0 results";
  } else {
    dom.searchCount.textContent = `${state.searchIndex + 1} / ${state.searchMatches.length}`;
  }
  const disabled = !state.searchMatches.length;
  dom.searchPrev.disabled = disabled;
  dom.searchNext.disabled = disabled;
}

function setCurrentSearchResult(dom: ViewerDom, state: ViewerState, index: number, scroll: boolean): void {
  state.searchMatches[state.searchIndex]?.classList.remove("search-current");
  state.searchIndex = index;
  const current = state.searchMatches[state.searchIndex];
  current?.classList.add("search-current");
  if (scroll) current?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function markSearchWords(turn: HTMLElement, query: string): void {
  const tokens = query.split(/\s+/).filter((token) => token.length >= 2);
  if (!tokens.length) return;
  turn.querySelectorAll<HTMLElement>(".word").forEach((word) => {
    const normalized = normalizeSearch(word.textContent).replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "");
    if (tokens.some((token) => normalized.includes(token))) word.classList.add("search-hit");
  });
}
