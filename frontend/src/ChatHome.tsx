import { useEffect, useMemo, useRef, useState } from "react";
import { formatDuration } from "./format";
import type { Health, JobSummary } from "./types";

interface Props {
  recent: JobSummary[];
  health: Health | null;
  uploading: boolean;
  progress: number;
  onOpen: (jobId: string) => void;
  onSubmit: (file: File, brief: string) => void;
}

const ACCEPT = ".mp4,.mov,.mkv,.webm,.m4v,.avi,.wav,.mp3,.m4a,.flac,.ogg";

const CHIPS = ["剪成 3 条竖屏", "只挑有争议的观点", "每条 30 秒内", "去掉停顿，加字幕"];

const RECENT_STATUS: Record<JobSummary["status"], string> = {
  uploaded: "已上传",
  transcribing: "转写中",
  transcribed: "已转写",
  planning: "选段中",
  planned: "待导出",
  rendering: "导出中",
  ready: "已完成",
  error: "出错",
};

export function ChatHome({ recent, health, uploading, progress, onOpen, onSubmit }: Props) {
  const [brief, setBrief] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textRef.current?.focus();
  }, []);

  const canSubmit = Boolean(file) && !uploading;
  const fileSize = useMemo(() => {
    if (!file) return "";
    const mb = file.size / 1024 / 1024;
    return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
  }, [file]);

  const pick = (files: FileList | null) => {
    const next = files?.[0];
    if (!next) return;
    setHint(null);
    setFile(next);
    window.setTimeout(() => textRef.current?.focus(), 0);
  };

  const submit = () => {
    if (!file) {
      setHint("先导入一段素材，再告诉我怎么剪");
      inputRef.current?.click();
      return;
    }
    onSubmit(file, brief.trim());
  };

  return (
    <div
      className={`chat-home${dragging ? " is-dragging" : ""}`}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        if (!uploading) pick(event.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        hidden
        onChange={(event) => pick(event.target.files)}
      />

      <div className="chat-hero">
        <div className="chat-mark">⏵</div>
        <h1>把素材丢进来，再说你想怎么剪</h1>
        <p>本地转写 · 逐句挑选 · 直接出片</p>
      </div>

      <div className="composer">
        <textarea
          ref={textRef}
          rows={3}
          value={brief}
          onChange={(event) => setBrief(event.target.value)}
          onPaste={(event) => {
            const pasted = event.clipboardData.files?.[0];
            if (pasted) {
              event.preventDefault();
              pick(event.clipboardData.files);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) submit();
          }}
          placeholder="说说你想剪成什么样…  不写也行，直接点「剪辑」先出一版初剪"
          disabled={uploading}
        />

        <div className="composer-bar">
          <button
            type="button"
            className="attach"
            onClick={() => inputRef.current?.click()}
            disabled={uploading}
          >
            ＋ 导入素材
          </button>
          {file && (
            <span className="file-chip" title={file.name}>
              {file.name}
              <em>{fileSize}</em>
              {!uploading && (
                <button type="button" onClick={() => setFile(null)} aria-label="移除素材">
                  ×
                </button>
              )}
            </span>
          )}
          <span className="composer-spacer" />
          <button type="button" className="primary send" onClick={submit} disabled={!canSubmit}>
            {uploading ? `导入中 ${Math.round(progress * 100)}%` : "剪辑 →"}
          </button>
        </div>

        {uploading && (
          <div className="composer-progress">
            <div style={{ width: `${Math.round(progress * 100)}%` }} />
          </div>
        )}
      </div>

      {hint && <div className="chat-hint">{hint}</div>}

      <div className="chat-chips">
        <span>试试：</span>
        {CHIPS.map((chip) => (
          <button
            type="button"
            key={chip}
            onClick={() => {
              setBrief((current) => (current ? `${current}，${chip}` : chip));
              textRef.current?.focus();
            }}
          >
            {chip}
          </button>
        ))}
      </div>

      {recent.length > 0 && (
        <div className="chat-recent">
          <div className="chat-recent-head">
            <strong>最近</strong>
            <span>点击继续</span>
          </div>
          <ul>
            {recent.slice(0, 5).map((item) => (
              <li key={item.id}>
                <button type="button" onClick={() => onOpen(item.id)}>
                  <span className="recent-name">{item.filename}</span>
                  <span className={`recent-status is-${item.status}`}>
                    {RECENT_STATUS[item.status]}
                  </span>
                  <span className="recent-meta">
                    {item.duration ? formatDuration(item.duration) : "—"}
                    {item.clips > 0 ? ` · ${item.clips} 个片段` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="chat-foot">
        {health
          ? `引擎 ${health.engine}${health.llm_enabled ? " · AI 选段已就绪" : " · 仅规则选段"}`
          : "正在连接本地服务…"}
      </p>
    </div>
  );
}