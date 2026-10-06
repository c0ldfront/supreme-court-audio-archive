import type { AnyObject, ViewerDom, ViewerState } from "./types";
import { avatar, profileFor } from "./profiles";
import { escapeHtml, seconds } from "./utils";

export function renderMeta(dom: ViewerDom, state: ViewerState): void {
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
}

export function renderSpeakers(dom: ViewerDom, state: ViewerState): void {
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

export function renderNow(dom: ViewerDom, profile: AnyObject, segment?: AnyObject, event?: AnyObject): void {
  dom.nowCard.innerHTML = `${avatar(profile)}
    <div>
      <div class="now-name">${escapeHtml(profile.display_name || profile.id)}</div>
      <div class="now-title">${escapeHtml(profile.title || profile.role || "")}</div>
      <div class="now-time">${event ? `${seconds(event.start)} - ${seconds(event.end)}` : segment ? `${seconds(segment.start)} - ${seconds(segment.end)}` : seconds(dom.audio.currentTime)}</div>
    </div>`;
}

export function renderInitialNow(dom: ViewerDom, state: ViewerState): void {
  renderNow(dom, profileFor(state, state.segments[0]?.speaker), state.segments[0]);
}
