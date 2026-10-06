import type { AnyObject, ViewerState } from "./types";
import { escapeHtml } from "./utils";

export function profileFor(state: ViewerState, id: string | null | undefined): AnyObject {
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

export function avatar(profile: AnyObject, className = "avatar"): string {
  const color = escapeHtml(profile.color || "#667085");
  if (profile.photo_url) {
    return `<span class="${className}" style="background:${color}"><img src="${escapeHtml(profile.photo_url)}" alt="${escapeHtml(profile.display_name || profile.id)}" loading="lazy"></span>`;
  }
  return `<span class="${className}" style="background:${color}">${escapeHtml(profile.initials || "?")}</span>`;
}
