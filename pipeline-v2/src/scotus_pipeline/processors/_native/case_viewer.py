#!/usr/bin/env python3
"""Build a static HTML viewer for a SCOTUS case timeline."""

from __future__ import annotations

import argparse
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TIMELINE_OUTPUT = Path("timeline-output")
DEFAULT_PROFILES_OUTPUT = Path("profiles-output")
DEFAULT_OUTPUT = Path("viewer-output")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def resolve_timeline(explicit: Path | None) -> Path:
    if explicit:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    raise ValueError("timeline is required")


def resolve_profiles(timeline_path: Path, explicit: Path | None, timeline: dict[str, Any]) -> Path:
    if explicit:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    raise ValueError(f"profiles is required for timeline {timeline_path}")


def json_for_script(payload: Any) -> str:
    return html.escape(json.dumps(payload, ensure_ascii=False), quote=False).replace("</", "<\\/")


def relative_asset(path: Path, output_path: Path) -> str:
    try:
        rel = os.path.relpath(path.resolve(), output_path.parent.resolve())
        return Path(rel).as_posix()
    except ValueError:
        return path.resolve().as_uri()


def format_case_title(case: dict[str, Any]) -> str:
    docket = case.get("docket") or "Unknown docket"
    name = case.get("name") or case.get("case_name") or "Unknown case"
    return f"{docket} · {name}"


def build_html(timeline: dict[str, Any], profiles: dict[str, Any], audio_src: str, created_at: str) -> str:
    title = format_case_title(timeline.get("case", {}))
    timeline_json = json_for_script(timeline)
    profiles_json = json_for_script(profiles)
    audio_src_html = html.escape(audio_src, quote=True)
    title_html = html.escape(title)
    created_html = html.escape(created_at)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title_html}</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --ink: #172033;
      --muted: #64748b;
      --line: #d8dee9;
      --panel: #ffffff;
      --panel-strong: #edf2f7;
      --panel-soft: #f8fafc;
      --field: #ffffff;
      --track-bg: #edf2f7;
      --track-line: #e2e8f0;
      --active-turn: #eff6ff;
      --active-word: #fde68a;
      --active-word-ink: #111827;
      --focus-ring: rgba(29, 78, 216, 0.16);
      --accent: #1d4ed8;
      --warn: #b45309;
      --shadow: 0 12px 36px rgba(15, 23, 42, 0.09);
    }}
    :root[data-theme="dark"] {{
      --bg: #0f172a;
      --ink: #e5e7eb;
      --muted: #94a3b8;
      --line: #334155;
      --panel: #111827;
      --panel-strong: #1e293b;
      --panel-soft: #162033;
      --field: #0f172a;
      --track-bg: #1e293b;
      --track-line: #334155;
      --active-turn: #172554;
      --active-word: #facc15;
      --active-word-ink: #111827;
      --focus-ring: rgba(96, 165, 250, 0.22);
      --accent: #60a5fa;
      --warn: #f59e0b;
      --shadow: 0 14px 40px rgba(0, 0, 0, 0.35);
      color-scheme: dark;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    button, input, textarea, audio {{ font: inherit; }}
    .app {{
      min-height: 100vh;
      display: grid;
      grid-template-columns: minmax(300px, 390px) minmax(0, 1fr);
    }}
    .sidebar {{
      background: var(--panel);
      border-right: 1px solid var(--line);
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
    }}
    .main {{
      padding: 20px;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }}
    .case-title {{
      margin: 0;
      font-size: 20px;
      line-height: 1.2;
      font-weight: 760;
    }}
    .case-header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
      gap: 8px 10px;
    }}
    .case-meta {{
      grid-column: 1 / -1;
      display: flex;
      flex-wrap: nowrap;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 13px;
      overflow-x: auto;
    }}
    .control-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    .pill {{
      border: 1px solid var(--line);
      background: var(--panel-strong);
      border-radius: 999px;
      padding: 4px 9px;
      white-space: nowrap;
      flex: 0 0 auto;
    }}
    .offset-control {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      background: var(--panel-strong);
      border-radius: 999px;
      padding: 3px 8px;
      white-space: nowrap;
    }}
    .offset-control input {{
      width: 64px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 2px 5px;
      background: var(--field);
      color: var(--ink);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }}
    .toggle-control {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      background: var(--panel-strong);
      border-radius: 999px;
      padding: 4px 9px;
      white-space: nowrap;
      flex: 0 0 auto;
    }}
    .toggle-control input {{
      margin: 0;
    }}
    .theme-toggle {{
      border: 1px solid var(--line);
      background: var(--panel-strong);
      color: var(--ink);
      border-radius: 8px;
      width: 34px;
      height: 34px;
      padding: 0;
      display: grid;
      place-items: center;
      cursor: pointer;
      font-size: 17px;
      line-height: 1;
    }}
    .theme-toggle:hover {{
      border-color: var(--accent);
    }}
    .active-speaker {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      overflow: hidden;
      box-shadow: var(--shadow);
    }}
    .active-portrait {{
      height: 260px;
      background: var(--panel-strong);
      display: grid;
      place-items: center;
      overflow: hidden;
    }}
    .active-portrait img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .avatar-fallback {{
      width: 96px;
      height: 96px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      color: white;
      font-size: 34px;
      font-weight: 800;
    }}
    .active-info {{
      padding: 14px;
      display: grid;
      gap: 4px;
      border-top: 4px solid var(--accent);
    }}
    .active-name {{
      font-weight: 780;
      font-size: 18px;
    }}
    .active-title, .active-time {{
      color: var(--muted);
      font-size: 13px;
    }}
    .audio {{
      width: 100%;
      display: block;
      min-height: 56px;
      height: 56px;
      flex: 0 0 56px;
    }}
    .section-title {{
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      font-weight: 780;
    }}
    .speaker-list {{
      display: grid;
      gap: 7px;
    }}
    .speaker-card {{
      width: 100%;
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: center;
      text-align: left;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--ink);
      padding: 7px;
      cursor: pointer;
    }}
    .speaker-card.active {{
      border-color: var(--accent);
      box-shadow: 0 0 0 2px var(--focus-ring);
    }}
    .speaker-thumb {{
      width: 44px;
      height: 44px;
      border-radius: 6px;
      overflow: hidden;
      display: grid;
      place-items: center;
      color: #fff;
      font-size: 14px;
      font-weight: 800;
      background: #64748b;
    }}
    .speaker-thumb img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
    }}
    .speaker-name {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 720;
      font-size: 13px;
    }}
    .speaker-role {{
      color: var(--muted);
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .turn-count {{
      color: var(--muted);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }}
    .timeline-panel, .transcript-panel, .qa-panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .panel-header {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }}
    .panel-title {{
      margin: 0;
      font-size: 15px;
      font-weight: 780;
    }}
    .search {{
      min-width: min(320px, 48vw);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      color: var(--ink);
      background: var(--field);
    }}
    .marker-note {{
      min-width: min(240px, 34vw);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 7px 9px;
      color: var(--ink);
      background: var(--field);
      font-size: 13px;
    }}
    .action-button {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
      color: var(--ink);
      padding: 7px 10px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .action-button:hover {{
      border-color: var(--accent);
    }}
    .action-button.primary {{
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }}
    .tracks {{
      padding: 14px 16px 18px;
      overflow-x: auto;
    }}
    .track {{
      display: grid;
      grid-template-columns: 150px minmax(720px, 1fr);
      gap: 12px;
      align-items: center;
      min-height: 32px;
      margin: 7px 0;
    }}
    .track-label {{
      font-size: 12px;
      font-weight: 720;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .track-line {{
      position: relative;
      height: 24px;
      background: var(--track-bg);
      border: 1px solid var(--track-line);
      border-radius: 6px;
      overflow: hidden;
    }}
    .segment-bar {{
      position: absolute;
      top: 0;
      bottom: 0;
      min-width: 2px;
      border: 0;
      cursor: pointer;
      opacity: 0.82;
    }}
    .segment-bar.active {{
      opacity: 1;
      box-shadow: inset 0 0 0 2px var(--ink);
    }}
    .event-bar {{
      position: absolute;
      top: 0;
      bottom: 0;
      min-width: 2px;
      border: 0;
      background: repeating-linear-gradient(
        45deg,
        rgba(100, 116, 139, 0.72),
        rgba(100, 116, 139, 0.72) 5px,
        rgba(148, 163, 184, 0.72) 5px,
        rgba(148, 163, 184, 0.72) 10px
      );
      cursor: pointer;
    }}
    .event-bar.active {{
      box-shadow: inset 0 0 0 2px var(--ink);
    }}
    .transcript-list {{
      max-height: 58vh;
      overflow: auto;
    }}
    .turn {{
      display: grid;
      grid-template-columns: 96px minmax(0, 1fr);
      gap: 14px;
      padding: 13px 16px;
      border-bottom: 1px solid var(--line);
      cursor: pointer;
    }}
    .turn:hover {{
      background: var(--panel-soft);
    }}
    .turn.active {{
      background: var(--active-turn);
      box-shadow: inset 4px 0 0 var(--accent);
    }}
    .turn-time {{
      color: var(--muted);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }}
    .turn-speaker {{
      font-weight: 760;
      font-size: 13px;
      margin-bottom: 4px;
    }}
    .turn-text {{
      line-height: 1.45;
      font-size: 14px;
    }}
    .word {{
      border-radius: 4px;
      padding: 1px 0;
      transition: background 120ms linear, color 120ms linear;
    }}
    .word.active {{
      background: var(--active-word);
      color: var(--active-word-ink);
      box-shadow: 0 0 0 2px var(--active-word);
    }}
    .word.qa-marked {{
      background: rgba(20, 184, 166, 0.26);
      box-shadow: 0 0 0 2px rgba(20, 184, 166, 0.26);
    }}
    .turn-source {{
      margin-top: 6px;
      font-size: 12px;
      color: var(--muted);
    }}
    .empty {{
      padding: 20px;
      color: var(--muted);
      text-align: center;
    }}
    .qa-list {{
      max-height: 260px;
      overflow: auto;
      border-top: 1px solid var(--line);
    }}
    .qa-marker {{
      display: grid;
      grid-template-columns: 84px minmax(0, 1fr) auto;
      gap: 10px;
      align-items: start;
      padding: 11px 16px;
      border-bottom: 1px solid var(--line);
    }}
    .qa-time {{
      color: var(--muted);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }}
    .qa-quote {{
      font-size: 13px;
      line-height: 1.38;
    }}
    .qa-meta {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
    }}
    .qa-actions {{
      display: flex;
      gap: 6px;
    }}
    .qa-icon {{
      border: 1px solid var(--line);
      background: var(--panel-strong);
      color: var(--ink);
      border-radius: 7px;
      width: 30px;
      height: 30px;
      display: grid;
      place-items: center;
      cursor: pointer;
    }}
    .qa-json {{
      width: 100%;
      min-height: 130px;
      resize: vertical;
      border: 0;
      border-top: 1px solid var(--line);
      padding: 12px 16px;
      background: var(--field);
      color: var(--ink);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
    }}
    @media (max-width: 900px) {{
      .app {{
        grid-template-columns: 1fr;
      }}
      .sidebar {{
        position: static;
        height: auto;
      }}
      .active-portrait {{
        height: 210px;
      }}
      .case-header {{
        align-items: center;
      }}
      .track {{
        grid-template-columns: 120px minmax(620px, 1fr);
      }}
      .panel-header {{
        align-items: stretch;
        flex-direction: column;
      }}
      .search {{
        min-width: 0;
        width: 100%;
      }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <header class="case-header">
        <h1 class="case-title" id="case-title">{title_html}</h1>
        <button class="theme-toggle" id="theme-toggle" type="button">◐</button>
        <div class="case-meta" id="case-meta"></div>
      </header>
      <audio class="audio" id="audio" controls preload="metadata" src="{audio_src_html}"></audio>
      <section class="active-speaker" id="active-speaker"></section>
      <section>
        <h2 class="section-title">Speakers</h2>
        <div class="speaker-list" id="speaker-list"></div>
      </section>
    </aside>
    <main class="main">
      <section class="timeline-panel">
        <div class="panel-header">
          <h2 class="panel-title">Argument Timeline</h2>
          <input class="search" id="search" type="search" placeholder="Filter transcript">
        </div>
        <div class="tracks" id="tracks"></div>
      </section>
      <section class="transcript-panel">
        <div class="panel-header">
          <h2 class="panel-title">Transcript Turns</h2>
          <div class="control-meta">
            <span class="pill" id="turn-summary"></span>
            <label class="offset-control">Word offset <input id="word-offset" type="number" min="-5" max="5" step="0.05" value="0.00">s</label>
            <span class="pill">Built {created_html}</span>
          </div>
        </div>
        <div class="transcript-list" id="transcript-list"></div>
      </section>
      <section class="qa-panel">
        <div class="panel-header">
          <h2 class="panel-title">QA Markers</h2>
          <div class="control-meta">
            <span class="pill" id="marker-summary">0 markers</span>
            <label class="toggle-control"><input id="qa-mode" type="checkbox"> QA mode</label>
            <label class="toggle-control"><input id="auto-follow" type="checkbox" checked> Follow</label>
            <label class="toggle-control"><input id="click-seek" type="checkbox" checked> Click seek</label>
            <input class="marker-note" id="marker-note" type="text" placeholder="Note">
            <button class="action-button primary" id="add-marker" type="button">Add Marker</button>
            <button class="action-button" id="export-markers" type="button">Export JSON</button>
            <button class="action-button" id="copy-markers" type="button">Copy JSON</button>
            <button class="action-button" id="clear-markers" type="button">Clear</button>
          </div>
        </div>
        <div class="qa-list" id="qa-list"></div>
        <textarea class="qa-json" id="qa-json" readonly></textarea>
      </section>
    </main>
  </div>
  <script id="timeline-data" type="application/json">{timeline_json}</script>
  <script id="profiles-data" type="application/json">{profiles_json}</script>
  <script>
    const timeline = JSON.parse(document.getElementById('timeline-data').textContent);
    const profilePayload = JSON.parse(document.getElementById('profiles-data').textContent);
    const segments = timeline.segments || [];
    const events = timeline.events || [];
    const nonSpeechEvents = events.filter((event) => event.type === 'non_speech');
    const profiles = Object.fromEntries((profilePayload.profiles || []).map((profile) => [profile.id, profile]));
    const duration = Number(timeline.duration || 0);
    const defaultWordHighlightOffsetSeconds = 0.00;
    const wordOffsetStorageKey = 'scotus-word-offset-seconds-v2';
    const audio = document.getElementById('audio');
    const speakerList = document.getElementById('speaker-list');
    const activeSpeaker = document.getElementById('active-speaker');
    const tracks = document.getElementById('tracks');
    const transcriptList = document.getElementById('transcript-list');
    const search = document.getElementById('search');
    const meta = document.getElementById('case-meta');
    const turnSummary = document.getElementById('turn-summary');
    const wordOffsetInput = document.getElementById('word-offset');
    const themeToggle = document.getElementById('theme-toggle');
    const addMarkerButton = document.getElementById('add-marker');
    const exportMarkersButton = document.getElementById('export-markers');
    const copyMarkersButton = document.getElementById('copy-markers');
    const clearMarkersButton = document.getElementById('clear-markers');
    const markerNote = document.getElementById('marker-note');
    const markerSummary = document.getElementById('marker-summary');
    const qaList = document.getElementById('qa-list');
    const qaJson = document.getElementById('qa-json');
    const qaModeInput = document.getElementById('qa-mode');
    const autoFollowInput = document.getElementById('auto-follow');
    const clickSeekInput = document.getElementById('click-seek');
    let currentTurnId = null;
    let currentSpeakerId = null;
    let currentWordKey = null;
    let wordHighlightOffsetSeconds = defaultWordHighlightOffsetSeconds;
    let qaMarkers = [];
    let savedSelectionRange = null;
    let qaMode = false;
    let autoFollow = true;
    let clickSeek = true;

    function fallbackProfile(id) {{
      return {{
        id,
        display_name: id,
        short_name: id,
        title: 'Speaker',
        role: 'speaker',
        initials: '?',
        color: '#64748b',
        stats: {{ turn_count: 0 }},
      }};
    }}

    function profileFor(id) {{
      return profiles[id] || fallbackProfile(id);
    }}

    function systemTheme() {{
      return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }}

    function applyTheme(theme) {{
      const nextTheme = theme === 'dark' ? 'dark' : 'light';
      document.documentElement.dataset.theme = nextTheme;
      localStorage.setItem('scotus-viewer-theme', nextTheme);
      themeToggle.textContent = nextTheme === 'dark' ? '☀' : '☾';
      themeToggle.title = nextTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
      themeToggle.setAttribute('aria-label', `Switch to ${{nextTheme === 'dark' ? 'light' : 'dark'}} theme`);
    }}

    function loadTheme() {{
      const saved = localStorage.getItem('scotus-viewer-theme');
      applyTheme(saved || systemTheme());
    }}

    function seconds(value) {{
      const total = Math.max(0, Math.floor(Number(value || 0)));
      const m = Math.floor(total / 60);
      const s = String(total % 60).padStart(2, '0');
      return `${{m}}:${{s}}`;
    }}

    function preciseSeconds(value) {{
      const total = Math.max(0, Number(value || 0));
      const m = Math.floor(total / 60);
      const s = (total - m * 60).toFixed(2).padStart(5, '0');
      return `${{m}}:${{s}}`;
    }}

    function markerStorageKey() {{
      const c = timeline.case || {{}};
      return `scotus-qa-markers:${{c.docket || 'case'}}:${{c.date_argued_iso || c.date_argued || ''}}`;
    }}

    function qaSettingsKey() {{
      const c = timeline.case || {{}};
      return `scotus-qa-settings:${{c.docket || 'case'}}:${{c.date_argued_iso || c.date_argued || ''}}`;
    }}

    function loadQaSettings() {{
      try {{
        const settings = JSON.parse(localStorage.getItem(qaSettingsKey()) || '{{}}');
        qaMode = Boolean(settings.qaMode);
        autoFollow = settings.autoFollow !== false;
        clickSeek = settings.clickSeek !== false;
      }} catch {{
        qaMode = false;
        autoFollow = true;
        clickSeek = true;
      }}
      qaModeInput.checked = qaMode;
      autoFollowInput.checked = autoFollow;
      clickSeekInput.checked = clickSeek;
    }}

    function saveQaSettings() {{
      localStorage.setItem(qaSettingsKey(), JSON.stringify({{ qaMode, autoFollow, clickSeek }}));
    }}

    function setQaMode(value) {{
      qaMode = Boolean(value);
      qaModeInput.checked = qaMode;
      if (qaMode) {{
        autoFollow = false;
        clickSeek = false;
        autoFollowInput.checked = false;
        clickSeekInput.checked = false;
      }}
      saveQaSettings();
    }}

    function setAutoFollow(value) {{
      autoFollow = Boolean(value);
      autoFollowInput.checked = autoFollow;
      saveQaSettings();
    }}

    function setClickSeek(value) {{
      clickSeek = Boolean(value);
      clickSeekInput.checked = clickSeek;
      saveQaSettings();
    }}

    function escapeHtml(value) {{
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({{
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }}[char]));
    }}

    function renderTimedText(segment) {{
      const parts = String(segment.text || '').split(/(\\s+)/);
      const wordCount = parts.filter((part) => part.trim()).length;
      const start = Number(segment.start || 0);
      const end = Number(segment.end || start);
      const duration = Math.max(0.01, end - start);
      const alignedWords = Array.isArray(segment.words) ? segment.words : [];
      let wordIndex = 0;

      return parts.map((part) => {{
        if (!part.trim()) return escapeHtml(part);
        const alignedWord = alignedWords[wordIndex] || {{}};
        const fallbackStart = start + duration * (wordIndex / Math.max(1, wordCount));
        const fallbackEnd = start + duration * ((wordIndex + 1) / Math.max(1, wordCount));
        const wordStart = Number.isFinite(Number(alignedWord.start)) ? Number(alignedWord.start) : fallbackStart;
        const wordEnd = Number.isFinite(Number(alignedWord.end)) ? Number(alignedWord.end) : fallbackEnd;
        const markedClass = isWordMarked(segment.turn_index, wordIndex) ? ' qa-marked' : '';
        const html = `<span class="word${{markedClass}}" data-turn="${{segment.turn_index}}" data-word="${{wordIndex}}" data-start="${{wordStart.toFixed(3)}}" data-end="${{wordEnd.toFixed(3)}}">${{escapeHtml(part)}}</span>`;
        wordIndex += 1;
        return html;
      }}).join('');
    }}

    function speakerAvatar(profile, size = 'small') {{
      if (profile.photo_url) {{
        return `<img src="${{profile.photo_url}}" alt="${{profile.display_name}}">`;
      }}
      return `<span class="avatar-fallback" style="background:${{profile.color}}">${{profile.initials || '?'}}</span>`;
    }}

    function nonSpeechProfile() {{
      return {{
        id: 'non_speech',
        display_name: 'No speech detected',
        short_name: 'No speech',
        title: 'Pause / room noise',
        role: 'event',
        initials: 'NS',
        color: '#64748b',
      }};
    }}

    function renderMeta() {{
      const c = timeline.case || {{}};
      const source = timeline.sources?.alignment;
      const items = [
        c.date_argued_iso || c.date_argued,
        `${{Math.round(duration / 60)}} min`,
        source?.available ? `${{source.applied_turn_count}} aligned turns` : 'rough timing',
        nonSpeechEvents.length ? `${{nonSpeechEvents.length}} non-speech gaps` : null,
      ].filter(Boolean);
      meta.innerHTML = items.map((item) => `<span class="pill">${{item}}</span>`).join('');
      turnSummary.textContent = `${{segments.length}} turns`;
    }}

    function renderActive(profile, segment) {{
      activeSpeaker.innerHTML = `
        <div class="active-portrait">${{speakerAvatar(profile, 'large')}}</div>
        <div class="active-info" style="border-top-color:${{profile.color}}">
          <div class="active-name">${{profile.display_name}}</div>
          <div class="active-title">${{profile.title || profile.role}}</div>
          <div class="active-time">${{segment ? `${{seconds(segment.start)}} - ${{seconds(segment.end)}}` : seconds(audio.currentTime)}}</div>
        </div>
      `;
    }}

    function renderNoSpeech(event) {{
      const profile = nonSpeechProfile();
      activeSpeaker.innerHTML = `
        <div class="active-portrait"><span class="avatar-fallback" style="background:${{profile.color}}">${{profile.initials}}</span></div>
        <div class="active-info" style="border-top-color:${{profile.color}}">
          <div class="active-name">${{profile.display_name}}</div>
          <div class="active-title">${{profile.title}}</div>
          <div class="active-time">${{event ? `${{seconds(event.start)}} - ${{seconds(event.end)}}` : seconds(audio.currentTime)}}</div>
        </div>
      `;
    }}

    function orderedProfiles() {{
      return Object.values(profiles).sort((a, b) => {{
        if (a.role !== b.role) return a.role === 'justice' ? -1 : 1;
        return (b.stats?.turn_count || 0) - (a.stats?.turn_count || 0);
      }});
    }}

    function renderSpeakers() {{
      const order = orderedProfiles();
      speakerList.innerHTML = order.map((profile) => `
        <button class="speaker-card" data-speaker="${{profile.id}}">
          <span class="speaker-thumb" style="background:${{profile.color}}">${{profile.photo_url ? `<img src="${{profile.photo_url}}" alt="">` : profile.initials}}</span>
          <span>
            <span class="speaker-name">${{profile.short_name || profile.display_name}}</span>
            <span class="speaker-role">${{profile.title || profile.role}}</span>
          </span>
          <span class="turn-count">${{profile.stats?.turn_count || 0}}</span>
        </button>
      `).join('');
      speakerList.querySelectorAll('button').forEach((button) => {{
        button.addEventListener('click', () => {{
          if (!clickSeek) return;
          const speaker = button.dataset.speaker;
          const segment = segments.find((item) => item.speaker === speaker);
          if (segment) seekTo(segment.start, {{ scrollTranscript: true }});
        }});
      }});
    }}

    function renderTracks() {{
      const bySpeaker = new Map();
      segments.forEach((segment) => {{
        if (!bySpeaker.has(segment.speaker)) bySpeaker.set(segment.speaker, []);
        bySpeaker.get(segment.speaker).push(segment);
      }});
      const orderedIds = orderedProfiles().map((profile) => profile.id);
      const orderRank = new Map(orderedIds.map((id, index) => [id, index]));
      const speakerIds = [...bySpeaker.keys()].sort((a, b) => {{
        const rankA = orderRank.has(a) ? orderRank.get(a) : Number.MAX_SAFE_INTEGER;
        const rankB = orderRank.has(b) ? orderRank.get(b) : Number.MAX_SAFE_INTEGER;
        if (rankA !== rankB) return rankA - rankB;
        return profileFor(a).display_name.localeCompare(profileFor(b).display_name);
      }});
      tracks.innerHTML = speakerIds.map((speaker) => {{
        const profile = profileFor(speaker);
        const bars = bySpeaker.get(speaker).map((segment) => {{
          const left = duration ? (segment.start / duration) * 100 : 0;
          const width = duration ? Math.max(0.08, ((segment.end - segment.start) / duration) * 100) : 0.08;
          return `<button class="segment-bar" data-turn="${{segment.turn_index}}" title="${{profile.display_name}} ${{seconds(segment.start)}}" style="left:${{left}}%;width:${{width}}%;background:${{profile.color}}"></button>`;
        }}).join('');
        return `<div class="track"><div class="track-label">${{profile.display_name}}</div><div class="track-line">${{bars}}</div></div>`;
      }}).join('') + renderEventTrack();
      tracks.querySelectorAll('button').forEach((button) => {{
        button.addEventListener('click', () => {{
          if (!clickSeek) return;
          if (button.dataset.turn) {{
            const segment = segments.find((item) => String(item.turn_index) === button.dataset.turn);
            if (segment) seekTo(segment.start, {{ scrollTranscript: true }});
          }}
          if (button.dataset.event) {{
            const event = nonSpeechEvents.find((item) => item.id === button.dataset.event);
            if (event) seekTo(event.start, {{ scrollTranscript: false }});
          }}
        }});
      }});
    }}

    function renderEventTrack() {{
      if (!nonSpeechEvents.length) return '';
      const bars = nonSpeechEvents.map((event) => {{
        const left = duration ? (event.start / duration) * 100 : 0;
        const width = duration ? Math.max(0.08, ((event.end - event.start) / duration) * 100) : 0.08;
        return `<button class="event-bar" data-event="${{event.id}}" title="${{escapeHtml(event.label || 'No speech')}} ${{seconds(event.start)}}" style="left:${{left}}%;width:${{width}}%"></button>`;
      }}).join('');
      return `<div class="track"><div class="track-label">No speech</div><div class="track-line">${{bars}}</div></div>`;
    }}

    function renderTranscript() {{
      const q = search.value.trim().toLowerCase();
      const filtered = q
        ? segments.filter((segment) => `${{segment.speaker}} ${{segment.text}}`.toLowerCase().includes(q))
        : segments;
      if (!filtered.length) {{
        transcriptList.innerHTML = '<div class="empty">No matching turns</div>';
        return;
      }}
      transcriptList.innerHTML = filtered.map((segment) => {{
        const profile = profileFor(segment.speaker);
        const source = segment.source || {{}};
        const status = source.alignment_status ? ` · ${{source.alignment_status}}` : '';
        return `
          <article class="turn" data-turn="${{segment.turn_index}}">
            <div class="turn-time">${{seconds(segment.start)}}<br>${{seconds(segment.end)}}</div>
            <div>
              <div class="turn-speaker" style="color:${{profile.color}}">${{profile.display_name}}</div>
              <div class="turn-text">${{renderTimedText(segment)}}</div>
              <div class="turn-source">${{source.timing || 'timing'}}${{status}}</div>
            </div>
          </article>
        `;
      }}).join('');
      transcriptList.querySelectorAll('.turn').forEach((turn) => {{
        turn.addEventListener('click', () => {{
          if (window.getSelection()?.toString().trim()) return;
          if (!clickSeek) return;
          const segment = segments.find((item) => String(item.turn_index) === turn.dataset.turn);
          if (segment) seekTo(segment.start, {{ scrollTranscript: true }});
        }});
      }});
      updateActive();
    }}

    function findSegment(time) {{
      if (findNonSpeechEvent(time)) return null;
      const matches = segments.filter((segment) => time >= Number(segment.start) && time < Number(segment.end));
      if (!matches.length) return null;
      return matches.find((segment) => segment.source?.timing === 'qa_marker_calibrated') || matches[0];
    }}

    function findNonSpeechEvent(time) {{
      return nonSpeechEvents.find((event) => time >= Number(event.start) && time < Number(event.end)) || null;
    }}

    function isWordMarked(turnIndex, wordIndex) {{
      return qaMarkers.some((marker) => {{
        const selection = marker.selection || {{}};
        if (String(marker.turn?.turn_index) !== String(turnIndex)) return false;
        if (!Number.isFinite(Number(selection.word_start_index)) || !Number.isFinite(Number(selection.word_end_index))) return false;
        return wordIndex >= Number(selection.word_start_index) && wordIndex <= Number(selection.word_end_index);
      }});
    }}

    function selectionInsideTranscript(range) {{
      const node = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
        ? range.commonAncestorContainer
        : range.commonAncestorContainer.parentElement;
      return Boolean(node && transcriptList.contains(node));
    }}

    function saveTranscriptSelection() {{
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0 || !selection.toString().trim()) return;
      const range = selection.getRangeAt(0);
      if (selectionInsideTranscript(range)) savedSelectionRange = range.cloneRange();
    }}

    function turnElementForRange(range) {{
      const startNode = range.startContainer.nodeType === Node.ELEMENT_NODE
        ? range.startContainer
        : range.startContainer.parentElement;
      const endNode = range.endContainer.nodeType === Node.ELEMENT_NODE
        ? range.endContainer
        : range.endContainer.parentElement;
      const startTurn = startNode?.closest?.('.turn');
      const endTurn = endNode?.closest?.('.turn');
      if (startTurn && startTurn === endTurn) return startTurn;
      return startTurn || endTurn || null;
    }}

    function collectSelectedWords(range, turnElement) {{
      const words = [...turnElement.querySelectorAll('.word')];
      return words.filter((word) => {{
        try {{
          return range.intersectsNode(word);
        }} catch {{
          return false;
        }}
      }}).map((word) => ({{
        index: Number(word.dataset.word),
        text: word.textContent,
        start: Number(word.dataset.start),
        end: Number(word.dataset.end),
      }})).filter((word) => Number.isFinite(word.index));
    }}

    function activeWordForSegment(segment) {{
      const index = estimatedWordIndex(segment, audio.currentTime + wordHighlightOffsetSeconds);
      if (index === null) return [];
      const word = transcriptList.querySelector(`.word[data-turn="${{segment.turn_index}}"][data-word="${{index}}"]`);
      if (!word) return [];
      return [{{
        index,
        text: word.textContent,
        start: Number(word.dataset.start),
        end: Number(word.dataset.end),
      }}];
    }}

    function markerExportPayload() {{
      const c = timeline.case || {{}};
      return {{
        schema: 'scotus_alignment_qa_markers.v1',
        created_at: new Date().toISOString(),
        case: {{
          docket: c.docket || null,
          name: c.name || c.case_name || null,
          term: c.term || null,
          date_argued_iso: c.date_argued_iso || null,
          audio_file: c.audio_file || null,
        }},
        timeline: {{
          duration_seconds: duration,
          timing_method: timeline.timing?.method || null,
          timing_confidence: timeline.timing?.confidence || null,
          source_word_alignment: timeline.sources?.official_word_alignment?.path || null,
        }},
        markers: qaMarkers,
      }};
    }}

    function saveMarkers() {{
      localStorage.setItem(markerStorageKey(), JSON.stringify(qaMarkers));
    }}

    function loadMarkers() {{
      try {{
        const saved = JSON.parse(localStorage.getItem(markerStorageKey()) || '[]');
        qaMarkers = Array.isArray(saved) ? saved : [];
      }} catch {{
        qaMarkers = [];
      }}
    }}

    function renderMarkers() {{
      markerSummary.textContent = `${{qaMarkers.length}} marker${{qaMarkers.length === 1 ? '' : 's'}}`;
      qaJson.value = JSON.stringify(markerExportPayload(), null, 2);
      if (!qaMarkers.length) {{
        qaList.innerHTML = '<div class="empty">No QA markers</div>';
        return;
      }}
      qaList.innerHTML = qaMarkers.map((marker) => `
        <article class="qa-marker" data-marker="${{marker.id}}">
          <div class="qa-time">${{escapeHtml(marker.expected_time)}}<br>${{escapeHtml(marker.turn?.speaker || '')}}</div>
          <div>
            <div class="qa-quote">${{escapeHtml(marker.phrase)}}</div>
            <div class="qa-meta">turn ${{escapeHtml(marker.turn?.turn_index)}} · ${{escapeHtml(marker.turn?.timing || 'timing')}}${{marker.note ? ` · ${{escapeHtml(marker.note)}}` : ''}}</div>
          </div>
          <div class="qa-actions">
            <button class="qa-icon" data-action="seek" title="Seek" type="button">▶</button>
            <button class="qa-icon" data-action="delete" title="Delete" type="button">×</button>
          </div>
        </article>
      `).join('');
      qaList.querySelectorAll('button').forEach((button) => {{
        button.addEventListener('click', () => {{
          const item = button.closest('.qa-marker');
          const marker = qaMarkers.find((entry) => entry.id === item?.dataset.marker);
          if (!marker) return;
          if (button.dataset.action === 'seek') seekTo(marker.expected_time_seconds, {{ forceScroll: true }});
          if (button.dataset.action === 'delete') {{
            qaMarkers = qaMarkers.filter((entry) => entry.id !== marker.id);
            saveMarkers();
            renderTranscript();
            renderMarkers();
          }}
        }});
      }});
    }}

    function addMarkerFromSelection() {{
      const range = savedSelectionRange;
      const selectedText = range ? range.toString().trim().replace(/\\s+/g, ' ') : '';
      const turnElement = range ? turnElementForRange(range) : null;
      const selectedSegment = turnElement
        ? segments.find((segment) => String(segment.turn_index) === turnElement.dataset.turn)
        : findSegment(audio.currentTime);
      if (!selectedSegment) return;

      const selectedWords = turnElement && range ? collectSelectedWords(range, turnElement) : activeWordForSegment(selectedSegment);
      const firstWord = selectedWords[0] || null;
      const lastWord = selectedWords[selectedWords.length - 1] || null;
      const phrase = selectedText || selectedWords.map((word) => word.text).join(' ') || String(selectedSegment.text || '').slice(0, 180);
      const source = selectedSegment.source || {{}};
      const c = timeline.case || {{}};
      const marker = {{
        id: `qa-${{Date.now()}}-${{Math.random().toString(16).slice(2, 8)}}`,
        created_at: new Date().toISOString(),
        expected_time_seconds: Number(audio.currentTime.toFixed(3)),
        expected_time: preciseSeconds(audio.currentTime),
        phrase,
        note: markerNote.value.trim(),
        case: {{
          docket: c.docket || null,
          name: c.name || c.case_name || null,
          date_argued_iso: c.date_argued_iso || null,
        }},
        turn: {{
          turn_index: selectedSegment.turn_index,
          speaker: selectedSegment.speaker,
          start_seconds: Number(Number(selectedSegment.start || 0).toFixed(3)),
          end_seconds: Number(Number(selectedSegment.end || 0).toFixed(3)),
          timing: source.timing || null,
          word_alignment_status: source.word_alignment_status || null,
          word_alignment_mean_score: source.word_alignment_mean_score ?? null,
        }},
        selection: {{
          word_start_index: firstWord ? firstWord.index : null,
          word_end_index: lastWord ? lastWord.index : null,
          word_count: selectedWords.length,
          word_start_seconds: firstWord && Number.isFinite(firstWord.start) ? Number(firstWord.start.toFixed(3)) : null,
          word_end_seconds: lastWord && Number.isFinite(lastWord.end) ? Number(lastWord.end.toFixed(3)) : null,
        }},
      }};
      qaMarkers.push(marker);
      markerNote.value = '';
      savedSelectionRange = null;
      window.getSelection()?.removeAllRanges();
      saveMarkers();
      renderTranscript();
      renderMarkers();
    }}

    function exportMarkers() {{
      const payload = markerExportPayload();
      qaJson.value = JSON.stringify(payload, null, 2);
      const c = timeline.case || {{}};
      const name = `scotus-qa-markers-${{String(c.docket || 'case').replace(/[^a-z0-9-]+/gi, '-')}}.json`;
      const blob = new Blob([qaJson.value + '\\n'], {{ type: 'application/json' }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    }}

    async function copyMarkers() {{
      qaJson.value = JSON.stringify(markerExportPayload(), null, 2);
      try {{
        await navigator.clipboard.writeText(qaJson.value);
      }} catch {{
        qaJson.select();
        document.execCommand('copy');
      }}
    }}

    function estimatedWordIndex(segment, time) {{
      const words = String(segment.text || '').match(/\\S+/g) || [];
      if (!words.length) return null;
      const alignedWords = Array.isArray(segment.words) ? segment.words : [];
      if (alignedWords.length) {{
        let nearest = null;
        for (let index = 0; index < alignedWords.length; index += 1) {{
          const word = alignedWords[index];
          const start = Number(word.start);
          const end = Number(word.end);
          if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
          if (time >= start && time < end) return index;
          if (time >= start) nearest = index;
        }}
        return nearest;
      }}
      const start = Number(segment.start || 0);
      const end = Number(segment.end || start);
      const ratio = Math.max(0, Math.min(0.999, (time - start) / Math.max(0.01, end - start)));
      return Math.floor(ratio * words.length);
    }}

    function updateActiveWord(segment) {{
      const wordIndex = estimatedWordIndex(segment, audio.currentTime + wordHighlightOffsetSeconds);
      const nextKey = wordIndex === null ? null : `${{segment.turn_index}}:${{wordIndex}}`;
      if (nextKey === currentWordKey) return;
      document.querySelectorAll('.word.active').forEach((word) => word.classList.remove('active'));
      currentWordKey = nextKey;
      if (wordIndex === null) return;
      const activeWord = transcriptList.querySelector(`.word[data-turn="${{segment.turn_index}}"][data-word="${{wordIndex}}"]`);
      if (activeWord) activeWord.classList.add('active');
    }}

    function scrollTranscriptToTurn(turnIndex) {{
      const turn = transcriptList.querySelector(`.turn[data-turn="${{turnIndex}}"]`);
      if (!turn) return;
      const listRect = transcriptList.getBoundingClientRect();
      const turnRect = turn.getBoundingClientRect();
      const targetTop = transcriptList.scrollTop
        + (turnRect.top - listRect.top)
        - Math.max(12, (transcriptList.clientHeight - turn.clientHeight) / 3);
      transcriptList.scrollTo({{ top: Math.max(0, targetTop), behavior: 'smooth' }});
    }}

    function loadWordOffset() {{
      const saved = Number(localStorage.getItem(wordOffsetStorageKey));
      wordHighlightOffsetSeconds = Number.isFinite(saved) ? saved : defaultWordHighlightOffsetSeconds;
      wordOffsetInput.value = wordHighlightOffsetSeconds.toFixed(2);
    }}

    function updateWordOffset(value) {{
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return;
      wordHighlightOffsetSeconds = Math.max(-5, Math.min(5, parsed));
      localStorage.setItem(wordOffsetStorageKey, String(wordHighlightOffsetSeconds));
      currentWordKey = null;
      const segment = findSegment(audio.currentTime);
      if (segment) updateActiveWord(segment);
    }}

    function seekTo(time, options = {{}}) {{
      audio.currentTime = Math.max(0, Number(time || 0));
      audio.play().catch(() => updateActive());
      updateActive();
      if (options.forceScroll || (options.scrollTranscript && autoFollow)) {{
        const segment = findSegment(audio.currentTime);
        if (segment) scrollTranscriptToTurn(segment.turn_index);
      }}
    }}

    function isTypingTarget(target) {{
      const element = target instanceof Element ? target : null;
      if (!element) return false;
      return Boolean(element.closest('input, textarea, select, button, a, [contenteditable="true"]'));
    }}

    function togglePlayback() {{
      if (audio.paused) {{
        audio.play().catch(() => updateActive());
      }} else {{
        audio.pause();
      }}
    }}

    function updateActive() {{
      const event = findNonSpeechEvent(audio.currentTime);
      if (event) {{
        if (currentTurnId !== `event:${{event.id}}`) {{
          currentTurnId = `event:${{event.id}}`;
          currentSpeakerId = 'non_speech';
          currentWordKey = null;
          renderNoSpeech(event);
          document.querySelectorAll('.speaker-card').forEach((card) => card.classList.remove('active'));
          document.querySelectorAll('.turn').forEach((turn) => turn.classList.remove('active'));
          document.querySelectorAll('.segment-bar').forEach((bar) => bar.classList.remove('active'));
          document.querySelectorAll('.event-bar').forEach((bar) => {{
            bar.classList.toggle('active', bar.dataset.event === event.id);
          }});
          document.querySelectorAll('.word.active').forEach((word) => word.classList.remove('active'));
        }}
        return;
      }}
      const segment = findSegment(audio.currentTime);
      if (!segment) return;
      const profile = profileFor(segment.speaker);
      if (currentSpeakerId !== segment.speaker) {{
        currentSpeakerId = segment.speaker;
        renderActive(profile, segment);
        document.querySelectorAll('.speaker-card').forEach((card) => {{
          card.classList.toggle('active', card.dataset.speaker === segment.speaker);
        }});
      }} else {{
        const time = activeSpeaker.querySelector('.active-time');
        if (time) time.textContent = `${{seconds(segment.start)}} - ${{seconds(segment.end)}}`;
      }}
      if (currentTurnId !== String(segment.turn_index)) {{
        currentTurnId = String(segment.turn_index);
        currentWordKey = null;
        document.querySelectorAll('.turn').forEach((turn) => {{
          turn.classList.toggle('active', turn.dataset.turn === currentTurnId);
        }});
        document.querySelectorAll('.segment-bar').forEach((bar) => {{
          bar.classList.toggle('active', bar.dataset.turn === currentTurnId);
        }});
        document.querySelectorAll('.event-bar').forEach((bar) => bar.classList.remove('active'));
        if (autoFollow) scrollTranscriptToTurn(segment.turn_index);
      }}
      updateActiveWord(segment);
    }}

    renderMeta();
    loadTheme();
    loadWordOffset();
    loadQaSettings();
    loadMarkers();
    renderSpeakers();
    renderTracks();
    renderTranscript();
    renderMarkers();
    renderActive(profileFor(segments[0]?.speaker), segments[0]);
    audio.addEventListener('timeupdate', updateActive);
    document.addEventListener('keydown', (event) => {{
      if (event.code !== 'Space' && event.key !== ' ') return;
      if (event.repeat || isTypingTarget(event.target)) return;
      event.preventDefault();
      togglePlayback();
    }});
    document.addEventListener('selectionchange', saveTranscriptSelection);
    wordOffsetInput.addEventListener('input', () => updateWordOffset(wordOffsetInput.value));
    themeToggle.addEventListener('click', () => {{
      applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    }});
    qaModeInput.addEventListener('change', () => setQaMode(qaModeInput.checked));
    autoFollowInput.addEventListener('change', () => setAutoFollow(autoFollowInput.checked));
    clickSeekInput.addEventListener('change', () => setClickSeek(clickSeekInput.checked));
    addMarkerButton.addEventListener('click', addMarkerFromSelection);
    exportMarkersButton.addEventListener('click', exportMarkers);
    copyMarkersButton.addEventListener('click', copyMarkers);
    clearMarkersButton.addEventListener('click', () => {{
      qaMarkers = [];
      saveMarkers();
      renderTranscript();
      renderMarkers();
    }});
    search.addEventListener('input', renderTranscript);
  </script>
</body>
</html>
"""


def build_viewer(args: argparse.Namespace) -> Path:
    timeline_path = resolve_timeline(args.timeline)
    timeline = load_json(timeline_path)
    if not isinstance(timeline, dict):
        raise RuntimeError(f"timeline must be a JSON object: {timeline_path}")

    profiles_path = resolve_profiles(timeline_path, args.profiles, timeline)
    profiles = load_json(profiles_path)
    if not isinstance(profiles, dict):
        raise RuntimeError(f"speaker profiles must be a JSON object: {profiles_path}")

    case = timeline.get("case", {})
    audio_file = case.get("audio_file") if isinstance(case, dict) else None
    if not audio_file:
        raise RuntimeError("timeline case metadata has no audio_file")

    audio_path = Path(str(audio_file))
    stem = audio_path.stem
    output = args.output or DEFAULT_OUTPUT / f"{stem}.html"
    audio_src = relative_asset(audio_path, output)
    html_doc = build_html(timeline, profiles, audio_src, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    write_text(output, html_doc)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a static HTML viewer for a SCOTUS case.")
    parser.add_argument("--timeline", type=Path, help="case timeline JSON")
    parser.add_argument("--profiles", type=Path, help="speaker profiles JSON")
    parser.add_argument("--output", type=Path, help="HTML output path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = build_viewer(args)
    print(f"case_viewer: {output}")
    print(f"open: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
