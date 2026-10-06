import type { AnyObject, ViewerDom, ViewerState } from "./types";
import { profileFor } from "./profiles";
import { escapeHtml, seconds } from "./utils";

export function renderTimedText(state: ViewerState, segment: AnyObject): string {
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
      const marked = isWordMarked(state, segment.turn_index, wordIndex) ? " qa-marked" : "";
      const html = `<span class="word${marked}" data-turn="${escapeHtml(segment.turn_index)}" data-word="${wordIndex}" data-start="${wordStart.toFixed(3)}" data-end="${wordEnd.toFixed(3)}">${escapeHtml(part)}</span>`;
      wordIndex += 1;
      return html;
    })
    .join("");
}

export function isWordMarked(state: ViewerState, turnIndex: unknown, wordIndex: number): boolean {
  return state.markers.some((marker) => {
    const selection = marker.selection || {};
    return (
      String(marker.turn?.turn_index) === String(turnIndex) &&
      wordIndex >= Number(selection.word_start_index) &&
      wordIndex <= Number(selection.word_end_index)
    );
  });
}

export function renderTranscript(dom: ViewerDom, state: ViewerState): void {
  const html = state.segments
    .map((segment) => {
      const profile = profileFor(state, segment.speaker);
      return `<article class="turn" data-turn="${escapeHtml(segment.turn_index)}" data-speaker="${escapeHtml(segment.speaker)}" data-text="${escapeHtml(segment.text || "")}">
        <header class="turn-head">
          <span class="turn-speaker" style="color:${escapeHtml(profile.color || "currentColor")}">${escapeHtml(profile.display_name || segment.speaker)}</span>
          <button class="turn-time text-button" type="button" data-time="${Number(segment.start || 0)}">${seconds(segment.start)} - ${seconds(segment.end)}</button>
        </header>
        <div class="turn-text" data-hydrated="false">${escapeHtml(segment.text || "")}</div>
      </article>`;
    })
    .join("");
  dom.transcriptList.innerHTML = html;
  state.turnElementByTurn = new Map(
    [...dom.transcriptList.querySelectorAll<HTMLElement>(".turn")].map((turn) => [String(turn.dataset.turn), turn]),
  );
  state.hydratedTurns.clear();
}

export function hydrateTurnWords(state: ViewerState, turn: HTMLElement | null): void {
  if (!turn) return;
  const turnIndex = String(turn.dataset.turn ?? "");
  if (!turnIndex || state.hydratedTurns.has(turnIndex)) return;
  const segment = state.segmentByTurn.get(turnIndex);
  const text = turn.querySelector<HTMLElement>(".turn-text");
  if (!segment || !text) return;
  text.innerHTML = renderTimedText(state, segment);
  text.dataset.hydrated = "true";
  state.hydratedTurns.add(turnIndex);
}

export function hydrateAllTurns(state: ViewerState): void {
  state.turnElementByTurn.forEach((turn) => hydrateTurnWords(state, turn));
}

export function estimateWordIndex(segment: AnyObject, time: number): number | null {
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

export function scrollToTurn(state: ViewerState, turnIndex: unknown): void {
  state.turnElementByTurn.get(String(turnIndex))?.scrollIntoView({ block: "center", behavior: "smooth" });
}
