import type {
  Aspect,
  AspectFit,
  ChatState,
  Health,
  Job,
  JobSummary,
  Clip,
  RenderQuality,
} from "./types";

export interface ExportPatch {
  aspect?: Aspect;
  fit?: AspectFit;
  subtitles?: boolean;
  trim_silence?: boolean;
  quality?: RenderQuality;
}

const BASE = "/api";

function detailOf(text: string): string {
  try {
    const parsed = JSON.parse(text);
    const detail = parsed?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? JSON.stringify(d)).join("; ");
    return text;
  } catch {
    return text;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(detailOf(text) || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  health: () => request<Health>("/health"),
  listJobs: () => request<JobSummary[]>("/jobs"),
  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  transcribe: (id: string) => request<Job>(`/jobs/${id}/transcribe`, { method: "POST" }),
  plan: (
    id: string,
    body: {
      mode: "rules" | "llm" | "manual";
      sentence_ids?: number[];
      limit?: number;
      trim_silence?: boolean;
      instruction?: string;
    },
  ) => request<Job>(`/jobs/${id}/plan`, json(body)),
  render: (
    id: string,
    opts?: {
      clipIds?: string[];
      aspect?: Aspect;
      fit?: AspectFit;
      quality?: RenderQuality;
      subtitles?: boolean;
    },
  ) =>
    request<Job>(
      `/jobs/${id}/render`,
      json({
        clip_ids: opts?.clipIds ?? null,
        aspect: opts?.aspect ?? "source",
        fit: opts?.fit ?? "crop",
        quality: opts?.quality ?? "standard",
        subtitles: opts?.subtitles ?? false,
      }),
    ),
  deleteJob: (id: string) => request<{ deleted: string }>(`/jobs/${id}`, { method: "DELETE" }),
  getChat: (id: string) => request<ChatState>(`/jobs/${id}/chat`),
  sendChat: (id: string, message: string, options?: ExportPatch) =>
    request<{ pending: boolean }>(
      `/jobs/${id}/chat`,
      json({ message, ...(options ?? {}) }),
    ),
  setOptions: (id: string, patch: ExportPatch) =>
    request<Job>(`/jobs/${id}/options`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
};

export function uploadFile(
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/jobs`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as Job);
        } catch {
          reject(new Error("上传响应无法解析"));
        }
      } else {
        reject(new Error(detailOf(xhr.responseText) || `HTTP ${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new Error("网络错误，上传失败"));
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
}

export const mediaUrl = (jobId: string) => `${BASE}/jobs/${jobId}/media`;

export const transcriptSrtUrl = (jobId: string) => `${BASE}/jobs/${jobId}/transcript.srt`;

export const draftUrl = (jobId: string, aspect: Aspect) =>
  `${BASE}/jobs/${jobId}/export/draft?aspect=${encodeURIComponent(aspect)}`;

export const clipSrtUrl = (jobId: string, clipId: string) =>
  `${BASE}/jobs/${jobId}/clips/${clipId}/srt`;

export const clipFileUrl = (jobId: string, clip: Clip) =>
  `${BASE}/jobs/${jobId}/clips/${clip.id}/file`;

export function subscribe(
  jobId: string,
  onJob: (job: Job) => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(`${BASE}/jobs/${jobId}/events`);
  source.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data) as { job: Job };
      if (payload.job) onJob(payload.job);
    } catch {
      // A malformed frame is not worth tearing the stream down for.
    }
  };
  source.onerror = () => onError?.();
  return () => source.close();
}