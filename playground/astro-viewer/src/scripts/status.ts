import type { ViewerDom } from "./types";

export function setStatus(dom: ViewerDom, status: "loading" | "ready" | "error", message?: string): void {
  dom.root.dataset.status = status;
  dom.status.hidden = status === "ready";
  if (message) dom.statusText.textContent = message;
}
