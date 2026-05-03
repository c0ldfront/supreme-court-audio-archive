import type { AnyObject, ViewerDom, ViewerState } from "./types";
import { seconds } from "./utils";

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

export function addMarkerFromSelection(dom: ViewerDom, state: ViewerState): void {
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

export function markerPayload(state: ViewerState): AnyObject {
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

export function showMarkers(dom: ViewerDom, state: ViewerState): void {
  dom.qaJson.value = JSON.stringify(markerPayload(state), null, 2);
  dom.qaDialog.showModal();
}
