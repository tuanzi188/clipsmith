export type JobStatus =
  | "uploaded"
  | "transcribing"
  | "transcribed"
  | "planning"
  | "planned"
  | "rendering"
  | "ready"
  | "error";

export type ClipStatus = "planned" | "rendering" | "ready" | "error";

export type Aspect = "source" | "9:16" | "1:1";

export type AspectFit = "crop" | "blur";

export type RenderQuality = "draft" | "standard" | "final";

export interface Setup {
  brief: string;
  aspect: Aspect;
  subtitles: boolean;
  trim: boolean;
}

export interface Sentence {
  id: number;
  start: number;
  end: number;
  text: string;
  speaker?: string | null;
}

export interface Transcript {
  language: string;
  duration: number;
  sentences: Sentence[];
  engine: string;
  meta?: Record<string, unknown>;
}

export interface Clip {
  id: string;
  start: number;
  end: number;
  sentence_ids: number[];
  hook: string;
  score: number;
  reason: string;
  title: string;
  caption: string;
  tags: string[];
  status: ClipStatus;
  file?: string | null;
  error?: string | null;
  ranges: number[][];
  removed_pauses: number;
  removed_seconds: number;
  voiceover_text: string;
}

export interface Job {
  id: string;
  filename: string;
  created_at: string;
  media_file: string;
  duration?: number | null;
  status: JobStatus;
  progress: number;
  message: string;
  error?: string | null;
  has_audio: boolean;
  transcript?: Transcript | null;
  clips: Clip[];
  export_aspect: Aspect;
  aspect_fit: AspectFit;
  export_subtitles: boolean;
  render_quality: RenderQuality;
  trim_silence: boolean;
  chat_pending: boolean;
}

export interface ChatAction {
  tool: string;
  summary: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  at: string;
  actions?: ChatAction[];
}

export interface ChatState {
  messages: ChatMessage[];
  pending: boolean;
}

export interface Health {
  ok: boolean;
  engine: string;
  llm_enabled: boolean;
  data_dir: string;
}

export interface JobSummary {
  id: string;
  filename: string;
  status: JobStatus;
  duration?: number | null;
  created_at: string;
  clips: number;
}