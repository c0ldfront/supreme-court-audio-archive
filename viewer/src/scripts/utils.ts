export function escapeHtml(value: unknown): string {
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

export function seconds(value: number | string | null | undefined, precise = false): string {
  const total = Math.max(0, Number(value || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const wholeSeconds = Math.floor(total % 60);
  const fraction = precise ? `.${String(Math.floor((total % 1) * 100)).padStart(2, "0")}` : "";
  if (hours) return `${hours}:${String(minutes).padStart(2, "0")}:${String(wholeSeconds).padStart(2, "0")}${fraction}`;
  return `${minutes}:${String(wholeSeconds).padStart(2, "0")}${fraction}`;
}

export function debounce<T extends (...args: any[]) => void>(fn: T, waitMs: number): T {
  let timer = 0;
  return ((...args: Parameters<T>) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), waitMs);
  }) as T;
}

export function normalizeSearch(value: unknown): string {
  return String(value ?? "").trim().replace(/\s+/g, " ").toLowerCase();
}
