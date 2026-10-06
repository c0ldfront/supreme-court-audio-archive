import type { AnyObject, TimelineHitRegion, ViewerDom, ViewerState } from "./types";
import { profileFor } from "./profiles";
import { escapeHtml, seconds } from "./utils";

const AXIS_HEIGHT = 24;
const LANE_HEIGHT = 23;
const EVENT_HEIGHT = 24;
const BAR_HEIGHT = 11;
const CANVAS_WIDTH = 2600;

export function findEvent(state: ViewerState, time: number): AnyObject | null {
  return state.events.find((event) => time >= Number(event.start || 0) && time < Number(event.end || event.start || 0)) || null;
}

export function findSegment(state: ViewerState, time: number): AnyObject | null {
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

export function renderTracks(dom: ViewerDom, state: ViewerState): void {
  const laneCount = Math.max(1, state.profiles.length);
  const width = timelineCssWidth(state);
  const labels = state.profiles
    .map((profile) => {
      return `<div class="lane-label" style="--speaker-color:${escapeHtml(profile.color || "#667085")}">${escapeHtml(profile.short_name || profile.display_name || profile.id)}</div>`;
    })
    .join("");
  const axisTicks = Array.from({ length: 11 }, (_, index) => {
    const left = index * 10;
    return `<div class="axis-tick" style="left:${left}%"><span>${seconds(Number(state.timeline.duration || 0) * (left / 100))}</span></div>`;
  }).join("");

  dom.tracks.innerHTML = `
    <div class="timeline-labels">
      ${labels}
      <div class="lane-label event-label">No speech</div>
    </div>
    <div class="timeline-scroll">
      <div class="timeline-canvas-wrap" style="--lane-count:${laneCount};width:${width}px">
        <div class="timeline-axis">${axisTicks}</div>
        <canvas class="timeline-canvas" width="${CANVAS_WIDTH}" height="${AXIS_HEIGHT + laneCount * LANE_HEIGHT + EVENT_HEIGHT}"></canvas>
      </div>
    </div>`;

  state.timelineCanvas = dom.tracks.querySelector<HTMLCanvasElement>(".timeline-canvas");
  buildTimelineHitRegions(state);
  drawTimeline(state);
}

export function buildTimelineHitRegions(state: ViewerState): void {
  const duration = Math.max(1, Number(state.timeline.duration || 1));
  const laneMap = new Map(state.profiles.map((profile, index) => [profile.id, index]));
  const laneCount = Math.max(1, state.profiles.length);
  const fallbackLane = laneCount - 1;
  const width = timelineCssWidth(state);
  const regions: TimelineHitRegion[] = [];

  for (const segment of state.segments) {
    const profile = profileFor(state, segment.speaker);
    const start = Number(segment.start || 0);
    const end = Number(segment.end || start);
    const lane = laneMap.get(segment.speaker) ?? fallbackLane;
    const x = (start / duration) * width;
    const y = AXIS_HEIGHT + lane * LANE_HEIGHT + 6;
    const barWidth = Math.max(2, ((end - start) / duration) * width);
    regions.push({
      type: "segment",
      turnIndex: String(segment.turn_index),
      time: start,
      speaker: segment.speaker,
      label: `${profile.display_name} ${seconds(start)} - ${seconds(end)}`,
      x,
      y,
      width: barWidth,
      height: BAR_HEIGHT,
      color: profile.color || "#667085",
    });
  }

  for (const event of state.events) {
    const start = Number(event.start || 0);
    const end = Number(event.end || start);
    const x = (start / duration) * width;
    const y = AXIS_HEIGHT + laneCount * LANE_HEIGHT + 6;
    regions.push({
      type: "event",
      time: start,
      label: `${event.label || "No speech"} ${seconds(start)} - ${seconds(end)}`,
      x,
      y,
      width: Math.max(2, ((end - start) / duration) * width),
      height: 12,
      color: "#7b8790",
    });
  }

  state.timelineHitRegions = regions;
}

export function drawTimeline(state: ViewerState): void {
  const canvas = state.timelineCanvas;
  if (!canvas) return;
  const context = canvas.getContext("2d");
  if (!context) return;

  const cssWidth = timelineCssWidth(state);
  const cssHeight = AXIS_HEIGHT + Math.max(1, state.profiles.length) * LANE_HEIGHT + EVENT_HEIGHT;
  const dpr = window.devicePixelRatio || 1;
  canvas.style.width = `${cssWidth}px`;
  canvas.style.height = `${cssHeight}px`;
  canvas.width = Math.floor(cssWidth * dpr);
  canvas.height = Math.floor(cssHeight * dpr);
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, cssWidth, cssHeight);

  for (const region of state.timelineHitRegions) {
    if (region.type === "event") {
      drawEventBar(context, region);
      continue;
    }
    context.fillStyle = region.color;
    context.globalAlpha = region.turnIndex === state.timelineActiveTurn ? 1 : 0.9;
    roundRect(context, region.x, region.y, region.width, region.height, 4);
    context.fill();
    if (region.turnIndex === state.timelineActiveTurn) {
      context.globalAlpha = 1;
      context.strokeStyle = cssVar("--active-word", "#d7a24a");
      context.lineWidth = 2;
      roundRect(context, region.x - 1, region.y - 3, region.width + 2, region.height + 6, 4);
      context.stroke();
    }
  }
  context.globalAlpha = 1;
  drawPlayhead(context, state, cssWidth, cssHeight);
}

export function timelineHitAt(state: ViewerState, clientX: number, clientY: number): TimelineHitRegion | null {
  const canvas = state.timelineCanvas;
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  const x = clientX - rect.left;
  const y = clientY - rect.top;
  for (let index = state.timelineHitRegions.length - 1; index >= 0; index -= 1) {
    const region = state.timelineHitRegions[index];
    if (x >= region.x && x <= region.x + region.width && y >= region.y - 4 && y <= region.y + region.height + 4) {
      return region;
    }
  }
  return null;
}

export function updateTimelineActive(state: ViewerState, turnIndex: string | null): void {
  if (state.timelineActiveTurn === turnIndex) return;
  state.timelineActiveTurn = turnIndex;
  drawTimeline(state);
}

export function updatePlayhead(dom: ViewerDom, state: ViewerState): void {
  state.timelinePlayheadSeconds = dom.audio.currentTime;
  drawTimeline(state);
}

export function scrollTimelineToTime(dom: ViewerDom, state: ViewerState, time: number): void {
  const duration = Number(dom.audio.duration || state.timeline.duration || 0);
  const scroller = dom.tracks.querySelector<HTMLElement>(".timeline-scroll");
  if (!scroller || !Number.isFinite(duration) || duration <= 0) return;
  const x = (Math.max(0, Math.min(duration, time)) / duration) * timelineCssWidth(state);
  scroller.scrollTo({
    left: Math.max(0, x - scroller.clientWidth * 0.5),
    behavior: "smooth",
  });
}

function timelineCssWidth(state: ViewerState): number {
  return Math.max(CANVAS_WIDTH, Math.ceil(Number(state.timeline.duration || 1) / 2.75));
}

function drawPlayhead(context: CanvasRenderingContext2D, state: ViewerState, width: number, height: number): void {
  const duration = Math.max(1, Number(state.timeline.duration || 1));
  const x = Math.max(0, Math.min(width, (state.timelinePlayheadSeconds / duration) * width));
  context.strokeStyle = cssVar("--active-word", "#d7a24a");
  context.lineWidth = 2;
  context.beginPath();
  context.moveTo(x, 4);
  context.lineTo(x, height - 4);
  context.stroke();
  context.fillStyle = cssVar("--active-word", "#d7a24a");
  context.beginPath();
  context.arc(x, 8, 4.5, 0, Math.PI * 2);
  context.fill();
}

function drawEventBar(context: CanvasRenderingContext2D, region: TimelineHitRegion): void {
  context.save();
  context.beginPath();
  roundRect(context, region.x, region.y, region.width, region.height, 4);
  context.clip();
  context.fillStyle = "rgba(123, 135, 144, 0.45)";
  context.fillRect(region.x, region.y, region.width, region.height);
  context.strokeStyle = "rgba(180, 190, 198, 0.55)";
  context.lineWidth = 2;
  for (let x = region.x - region.height; x < region.x + region.width + region.height; x += 8) {
    context.beginPath();
    context.moveTo(x, region.y + region.height);
    context.lineTo(x + region.height, region.y);
    context.stroke();
  }
  context.restore();
}

function roundRect(context: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number): void {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + r, y);
  context.arcTo(x + width, y, x + width, y + height, r);
  context.arcTo(x + width, y + height, x, y + height, r);
  context.arcTo(x, y + height, x, y, r);
  context.arcTo(x, y, x + width, y, r);
  context.closePath();
}

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}
