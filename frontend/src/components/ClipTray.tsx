import { useState } from "react";
import type { Clip, Job } from "../types";
import { formatDuration, formatTime } from "../format";
import { clipFileUrl, clipSrtUrl } from "../api";

interface Props {
  job: Job;
  disabled: boolean;
  onRender: (clipIds?: string[]) => void;
  onSeek: (seconds: number) => void;
  draftUrl: string;
}

const STATUS_LABEL: Record<Clip["status"], string> = {
  planned: "待导出",
  rendering: "导出中",
  ready: "可预览",
  error: "失败",
};

function publishText(clip: Clip): string {
  return [clip.title || clip.hook, clip.caption, clip.tags.map((tag) => `#${tag}`).join(" ")]
    .filter((part) => part && part.trim())
    .join("\n");
}

export function ClipTray({ job, disabled, onRender, onSeek, draftUrl }: Props) {
  const [copied, setCopied] = useState<string | null>(null);
  const clips = job.clips;

  const pending = clips.filter((clip) => clip.status === "planned" || clip.status === "error");

  const copy = async (clip: Clip) => {
    const text = publishText(clip);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(clip.id);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      setCopied(null);
    }
  };

  return (
    <section className="clip-tray">
      <div className="clip-tray-head">
        <div>
          <h2 className="studio-panel-title"><span aria-hidden="true">03</span> 成片与候选</h2>
          <span>
            {clips.length} 个 ·{" "}
            {clips.filter((clip) => clip.status === "ready").length} 个已导出
          </span>
        </div>
        {clips.length > 0 && <div className="clip-tray-actions">
          <a className="ghost link" href={draftUrl} download="clipsmith-draft.zip">
            剪映草稿
          </a>
          {pending.length > 0 && <button
            type="button"
            className="primary"
            disabled={disabled || pending.length === 0}
            onClick={() => onRender()}
          >
            导出{pending.length ? ` ${pending.length} 个` : ""}
          </button>}
        </div>}
      </div>

      <div className="clip-grid">
        {!clips.length && <div className="clip-empty"><span aria-hidden="true">▱</span>暂无片段</div>}
        {clips.map((clip, clipIndex) => (
          <article className={`clip-card is-${clip.status}`} key={clip.id}>
            <header>
              <span className="score">片段 {String(clipIndex + 1).padStart(2, "0")}</span>
              <span className={`clip-status is-${clip.status}`}>
                {STATUS_LABEL[clip.status]}
              </span>
            </header>
            <h3>{clip.title || clip.hook || "未命名片段"}</h3>
            {clip.title && clip.hook && clip.title !== clip.hook && (
              <p className="clip-hook">{clip.hook}</p>
            )}
            {clip.caption && <p className="clip-caption">{clip.caption}</p>}
            <p className="clip-reason">{clip.reason}</p>
            {clip.tags.length > 0 && (
              <div className="tags">
                {clip.tags.map((tag) => (
                  <span className="tag" key={tag}>
                    #{tag}
                  </span>
                ))}
              </div>
            )}
            <div className="clip-meta">
              <span>
                {formatTime(clip.start)} – {formatTime(clip.end)}
              </span>
              <span>{formatDuration(clip.end - clip.start)}</span>
            </div>
            {clip.removed_pauses > 0 && (
              <div className="clip-cut">
                去停顿 {clip.removed_pauses} 处 · 缩短 {formatDuration(clip.removed_seconds)}
              </div>
            )}

            {clip.status === "ready" ? (
              <>
                <video
                  src={clipFileUrl(job.id, clip)}
                  controls
                  preload="metadata"
                  playsInline
                />
                <div className="clip-actions">
                  <a href={clipFileUrl(job.id, clip)} download={`clip-${clip.id}.mp4`}>
                    下载 MP4
                  </a>
                  <a href={clipSrtUrl(job.id, clip.id)} download={`clip-${clip.id}.srt`}>
                    SRT
                  </a>
                  <button type="button" className="ghost" onClick={() => onSeek(clip.start)}>
                    定位
                  </button>
                </div>
              </>
            ) : null}

            <div className="clip-actions">
              <button type="button" className="ghost" onClick={() => void copy(clip)}>
                {copied === clip.id ? "已复制" : "复制文案"}
              </button>
            </div>

            {clip.status === "error" && (
              <div className="clip-error">
                <span>{clip.error || "导出失败"}</span>
                <button
                  type="button"
                  className="ghost"
                  disabled={disabled}
                  onClick={() => onRender([clip.id])}
                >
                  重试
                </button>
              </div>
            )}

            {clip.status === "rendering" && <div className="clip-shimmer" />}
          </article>
        ))}
      </div>
    </section>
  );
}
