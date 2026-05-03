export type AnyObject = Record<string, any>;

export type ViewerDom = {
  root: HTMLElement;
  audio: HTMLAudioElement;
  caseTitle: HTMLElement;
  caseMeta: HTMLElement;
  currentTime: HTMLElement;
  duration: HTMLElement;
  seek: HTMLInputElement;
  wordOffset: HTMLInputElement;
  autoFollow: HTMLInputElement;
  qaMode: HTMLInputElement;
  nowCard: HTMLElement;
  speakerList: HTMLElement;
  tracks: HTMLElement;
  transcriptList: HTMLElement;
  timelineSummary: HTMLElement;
  search: HTMLInputElement;
  searchCount: HTMLElement;
  searchPrev: HTMLButtonElement;
  searchNext: HTMLButtonElement;
  themeToggle: HTMLButtonElement;
  exportMarkers: HTMLButtonElement;
  qaDialog: HTMLDialogElement;
  qaJson: HTMLTextAreaElement;
  status: HTMLElement;
  statusText: HTMLElement;
};

export type ActiveElements = {
  turn: HTMLElement | null;
  speaker: HTMLElement | null;
  word: HTMLElement | null;
};

export type TimelineHitRegion = {
  type: "segment" | "event";
  turnIndex?: string;
  time: number;
  speaker?: string;
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
};

export type ViewerState = {
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
  searchQuery: string;
  searchMatches: HTMLElement[];
  searchIndex: number;
  segmentByTurn: Map<string, AnyObject>;
  turnElementByTurn: Map<string, HTMLElement>;
  speakerElementById: Map<string, HTMLElement>;
  hydratedTurns: Set<string>;
  timelineHitRegions: TimelineHitRegion[];
  timelineCanvas: HTMLCanvasElement | null;
  timelineActiveTurn: string | null;
  timelinePlayheadSeconds: number;
};
