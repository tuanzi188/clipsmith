import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, draftUrl, subscribe, transcriptSrtUrl } from "./api";
import { ClipTray } from "./components/ClipTray";
import { TimelineStrip } from "./components/TimelineStrip";
import { TranscriptPane } from "./components/TranscriptPane";
import { ChatPanel } from "./components/ChatPanel";
import { VideoStage } from "./components/VideoStage";
import { errorMessage, formatDuration } from "./format";
import type { Aspect, AspectFit, Health, Job, RenderQuality, Setup } from "./types";

const BUSY: Job["status"][] = ["transcribing", "planning", "rendering"];

const STATUS_TEXT: Record<Job["status"], string> = {
  uploaded: "已上传",
  transcribing: "转写中",
  transcribed: "转写完成",
  planning: "选段中",
  planned: "待导出",
  rendering: "导出中",
  ready: "完成",
  error: "出错",
};

interface Props {
  jobId: string;
  health: Health | null;
  visible: boolean;
  setup?: Setup;
  onMeta: (job: Job) => void;
  onClose: (jobId: string) => void;
}

export function Workspace({ jobId, health, visible, setup, onMeta, onClose }: Props) {
  const [job, setJob] = useState<Job | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [time, setTime] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [useLLM, setUseLLM] = useState(false);
  const [instruction, setInstruction] = useState(setup?.brief ?? "");
  const [briefDismissed, setBriefDismissed] = useState(false);
  const [query, setQuery] = useState("");
  const [speaker, setSpeaker] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const autoStarted = useRef<string | null>(null);
  const autoChat = useRef<string | null>(null);
  const pendingSeek = useRef<number | null>(null);

  // -- load / live updates ------------------------------------------------
  useEffect(() => {
    setJob(null);
    setLoadError(null);
    autoStarted.current = null;
    pendingSeek.current = null;
    api
      .getJob(jobId)
      .then((value) => {
        setJob(value);
        onMeta(value);
      })
      .catch((err) => setLoadError(errorMessage(err)));
  }, [jobId]);

  // Only the visible tab holds an EventSource: browsers cap ~6 connections per
  // origin, so one SSE per tab (hidden ones included) starves fetches and
  // media requests. Hidden tabs fall back to the busy poller below.
  useEffect(() => {
    if (!job?.id || !visible) return;
    return subscribe(
      job.id,
      (value) => {
        setJob(value);
        onMeta(value);
      },
      () => {
        // Stream dropped: pull one fresh snapshot. The stream re-connects on
        // its own and replays the current state when it does.
        api
          .getJob(job.id)
          .then((value) => {
            setJob(value);
            onMeta(value);
          })
          .catch(() => undefined);
      },
    );
  }, [job?.id, visible]);

  // A tab that just came back from the background has been tracking busy
  // states through polling only; fetch one snapshot so nothing looks stale.
  const prevVisible = useRef(visible);
  useEffect(() => {
    if (visible && !prevVisible.current && job?.id) {
      api
        .getJob(job.id)
        .then((value) => {
          setJob(value);
          onMeta(value);
        })
        .catch(() => undefined);
    }
    prevVisible.current = visible;
  }, [visible, job?.id]);

  useEffect(() => {
    if (!job?.id || !BUSY.includes(job.status)) return;
    const id = job.id;
    const timer = window.setInterval(() => {
      api
        .getJob(id)
        .then((value) => {
          setJob(value);
          onMeta(value);
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [job?.id, job?.status]);

  useEffect(() => {
    if (!visible) videoRef.current?.pause();
  }, [visible]);

  // -- auto transcribe right after upload ---------------------------------
  useEffect(() => {
    if (!job || job.status !== "uploaded" || autoStarted.current === job.id) return;
    autoStarted.current = job.id;
    api
      .transcribe(job.id)
      .then((value) => {
        setJob(value);
        onMeta(value);
      })
      .catch((err) => setError(errorMessage(err)));
  }, [job]);

  // -- export options live on the job, so the agent and the UI share them ---
  const aspect = (job?.export_aspect ?? "source") as Aspect;
  const fit = (job?.aspect_fit ?? "crop") as AspectFit;
  const quality = (job?.render_quality ?? "standard") as RenderQuality;
  const subtitles = job?.export_subtitles ?? false;
  const trimSilence = job?.trim_silence ?? true;

  // Serialize option PATCHes: responses apply in request order, so a fast
  // stale response can never overwrite a newer choice made right after it.
  const optionsChain = useRef<Promise<void>>(Promise.resolve());
  const updateOptions = (patch: {
    aspect?: Aspect;
    fit?: AspectFit;
    subtitles?: boolean;
    trim_silence?: boolean;
    quality?: RenderQuality;
  }) => {
    if (!job) return;
    const jobId = job.id;
    setError(null);
    optionsChain.current = optionsChain.current
      .then(async () => {
        const value = await api.setOptions(jobId, patch);
        setJob(value);
        onMeta(value);
      })
      .catch((err) => setError(errorMessage(err)));
  };

  // A brief typed on the chat home becomes the first message to the agent, once
  // transcription is done. It carries the settings parsed from that sentence.
  useEffect(() => {
    if (!job || !setup?.brief || !job.transcript) return;
    // Without an LLM there is no agent to run the brief; leave the transcript
    // screen with its explicit 自动选段 button instead of raising a 400.
    if (!health?.llm_enabled) return;
    if (autoChat.current === job.id) return;
    autoChat.current = job.id;
    let cancelled = false;
    (async () => {
      try {
        const state = await api.getChat(job.id);
        if (cancelled || state.messages.length > 0 || state.pending) return;
        await api.sendChat(job.id, setup.brief, {
          aspect: setup.aspect,
          subtitles: setup.subtitles,
          trim_silence: setup.trim,
        });
      } catch (err) {
        autoChat.current = null;
        if (!cancelled) setError(errorMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [job?.id, job?.transcript?.sentences.length, setup?.brief, health?.llm_enabled]);

  // -- selection ----------------------------------------------------------
  useEffect(() => {
    setSelected(new Set());
    setAnchor(null);
    setQuery("");
    setSpeaker(null);
  }, [jobId, job?.transcript?.engine, job?.transcript?.sentences.length]);

  const toggleSentence = useCallback(
    (id: number, shiftKey: boolean) => {
      setSelected((previous) => {
        const next = new Set(previous);
        if (shiftKey && anchor !== null) {
          const [from, to] = anchor <= id ? [anchor, id] : [id, anchor];
          for (let current = from; current <= to; current += 1) next.add(current);
        } else if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
        }
        return next;
      });
      setAnchor(id);
    },
    [anchor],
  );

  const activeId = useMemo(() => {
    if (!job?.transcript) return null;
    const active = job.transcript.sentences.find(
      (sentence) => time >= sentence.start && time < sentence.end,
    );
    return active?.id ?? null;
  }, [job?.transcript, time]);

  const seek = useCallback((seconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    // The transcript can render before the media metadata arrives; assigning
    // currentTime before that throws. Park the seek until the first frame.
    if (video.readyState < 1) {
      pendingSeek.current = seconds;
      return;
    }
    try {
      video.currentTime = seconds;
    } catch {
      return;
    }
    void video.play().catch(() => undefined);
  }, []);

  const flushPendingSeek = useCallback(() => {
    const target = pendingSeek.current;
    if (target === null) return;
    pendingSeek.current = null;
    seek(target);
  }, [seek]);

  // -- keyboard shortcuts --------------------------------------------------
  useEffect(() => {
    if (!visible) return;
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      const video = videoRef.current;
      if (!video) return;

      switch (event.key) {
        case " ":
          event.preventDefault();
          if (video.paused) void video.play().catch(() => undefined);
          else video.pause();
          break;
        case "ArrowLeft":
          event.preventDefault();
          seek(Math.max(0, video.currentTime - (event.shiftKey ? 5 : 1)));
          break;
        case "ArrowRight":
          event.preventDefault();
          seek(video.currentTime + (event.shiftKey ? 5 : 1));
          break;
        case "x":
        case "X":
          if (activeId !== null) toggleSentence(activeId, false);
          break;
        case "Escape":
          setSelected(new Set());
          setAnchor(null);
          break;
        case "/":
          event.preventDefault();
          searchRef.current?.focus();
          break;
        default:
          break;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [visible, activeId, seek, toggleSentence]);

  // -- derived ------------------------------------------------------------
  const speakers = useMemo(() => {
    const set = new Set<string>();
    for (const sentence of job?.transcript?.sentences ?? []) {
      if (sentence.speaker) set.add(sentence.speaker);
    }
    return [...set];
  }, [job?.transcript]);

  const visibleSentences = useMemo(() => {
    const sentences = job?.transcript?.sentences ?? [];
    const needle = query.trim().toLowerCase();
    return sentences.filter((sentence) => {
      if (speaker && sentence.speaker !== speaker) return false;
      if (!needle) return true;
      return sentence.text.toLowerCase().includes(needle);
    });
  }, [job?.transcript, query, speaker]);

  // -- actions ------------------------------------------------------------
  const busy = pending || (job ? BUSY.includes(job.status) || job.chat_pending : false);

  const run = async (action: () => Promise<Job>) => {
    setPending(true);
    setError(null);
    try {
      const value = await action();
      setJob(value);
      onMeta(value);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  };

  const planAuto = () => {
    if (!job) return;
    void run(() =>
      api.plan(job.id, {
        mode: useLLM && health?.llm_enabled ? "llm" : "rules",
        trim_silence: trimSilence,
        instruction:
          useLLM && health?.llm_enabled && instruction.trim()
            ? instruction.trim()
            : undefined,
      }),
    );
  };

  const planManual = () => {
    if (!job) return;
    if (selected.size === 0) {
      setError("先在右侧转写稿里选中几句");
      return;
    }
    void run(() =>
      api.plan(job.id, {
        mode: "manual",
        sentence_ids: [...selected].sort((a, b) => a - b),
        trim_silence: trimSilence,
      }),
    );
  };

  const render = (clipIds?: string[]) => {
    if (!job) return;
    void run(() => api.render(job.id, { clipIds, aspect, fit, quality, subtitles }));
  };

  const deleteJob = () => {
    if (!job) return;
    if (!window.confirm("删除任务和本地素材文件？")) return;
    void api
      .deleteJob(job.id)
      .then(() => onClose(job.id))
      .catch((err) => setError(errorMessage(err)));
  };

  const retryTranscribe = () => {
    if (!job) return;
    void run(() => api.transcribe(job.id));
  };

  if (loadError) {
    return (
      <div className={visible ? "workspace-root" : "workspace-root is-hidden"}>
        <div className="workspace-loading">
          <p>{loadError}</p>
          <button type="button" className="ghost" onClick={() => onClose(jobId)}>
            关闭标签
          </button>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className={visible ? "workspace-root" : "workspace-root is-hidden"}>
        <div className="workspace-loading">
          <div className="pulse" />
          <p>加载任务…</p>
        </div>
      </div>
    );
  }

  const transcribed = Boolean(job.transcript);
  const selectedCount = selected.size;

  return (
    <div className={visible ? "workspace-root" : "workspace-root is-hidden"}>
      {error && (
        <div className="banner error">
          <span>{error}</span>
          <button type="button" onClick={() => setError(null)}>
            关闭
          </button>
        </div>
      )}

      <main className="workspace">
        <aside className="side">
          <VideoStage
            jobId={job.id}
            videoRef={videoRef}
            currentTime={time}
            onTimeUpdate={setTime}
            onLoadedMetadata={flushPendingSeek}
            filename={job.filename}
            duration={job.duration}
          />

          <div className="status-card">
            <div className="status-row">
              <span className={`status-dot is-${job.status}`} />
              <strong>{job.message || STATUS_TEXT[job.status]}</strong>
              <span>{job.progress}%</span>
            </div>
            <div className="progress">
              <div style={{ width: `${job.progress}%` }} />
            </div>
            {job.error && <p className="status-error">{job.error}</p>}
            {job.status === "error" && (
              <button type="button" className="ghost" onClick={retryTranscribe}>
                重新转写
              </button>
            )}
            <div className="status-extra">
              <span>
                {job.transcript
                  ? `${job.transcript.sentences.length} 句 · ${formatDuration(
                      job.transcript.duration,
                    )}`
                  : "等待转写"}
              </span>
              {job.transcript && <span>引擎 {job.transcript.engine}</span>}
            </div>
          </div>

          <div className="shortcut-card">
            <span>
              <kbd>Space</kbd> 播放
            </span>
            <span>
              <kbd>←</kbd>
              <kbd>→</kbd> 跳转
            </span>
            <span>
              <kbd>X</kbd> 选句
            </span>
            <span>
              <kbd>/</kbd> 搜索
            </span>
          </div>

          <button type="button" className="ghost danger" onClick={deleteJob}>
            删除任务与文件
          </button>
        </aside>

        <section className="editor">
          <div className="editor-head">
            <div>
              <h2>转写稿</h2>
              <span>
                {selectedCount > 0 ? `已选 ${selectedCount} 句` : "选中连续的句子即可成片"}
              </span>
            </div>
            <div className="editor-actions">
              {health?.llm_enabled ? (
                <label className="switch">
                  <input
                    type="checkbox"
                    checked={useLLM}
                    onChange={(event) => setUseLLM(event.target.checked)}
                  />
                  <span>AI 选段</span>
                </label>
              ) : (
                <span className="muted-hint">未配置 LLM，使用规则选段</span>
              )}
              <label className="switch">
                <input
                  type="checkbox"
                  checked={trimSilence}
                  onChange={() => updateOptions({ trim_silence: !trimSilence })}
                />
                <span>去停顿</span>
              </label>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={subtitles}
                  onChange={() => updateOptions({ subtitles: !subtitles })}
                />
                <span>烧字幕</span>
              </label>
              <select
                className="aspect-select"
                value={aspect}
                onChange={(event) =>
                  updateOptions({ aspect: event.target.value as Aspect })
                }
                title="导出画幅"
              >
                <option value="source">原画幅</option>
                <option value="9:16">9:16 竖屏</option>
                <option value="1:1">1:1 方形</option>
              </select>
              {aspect !== "source" && (
                <select
                  className="aspect-select"
                  value={fit}
                  onChange={(event) =>
                    updateOptions({ fit: event.target.value as AspectFit })
                  }
                  title="画面适配方式"
                >
                  <option value="crop">居中裁切</option>
                  <option value="blur">模糊背景</option>
                </select>
              )}
              <select
                className="aspect-select"
                value={quality}
                onChange={(event) =>
                  updateOptions({ quality: event.target.value as RenderQuality })
                }
                title="成片画质"
              >
                <option value="draft">草稿画质</option>
                <option value="standard">标准画质</option>
                <option value="final">终版画质</option>
              </select>
              {selectedCount > 0 && (
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setSelected(new Set());
                    setAnchor(null);
                  }}
                >
                  清空选择
                </button>
              )}
              <button
                type="button"
                className="secondary"
                disabled={busy || !transcribed || selectedCount === 0}
                onClick={planManual}
              >
                按选中切
              </button>
              <button
                type="button"
                className="primary"
                disabled={busy || !transcribed}
                onClick={planAuto}
              >
                自动选段
              </button>
            </div>
          </div>

          {setup?.brief &&
            !briefDismissed &&
            !job.chat_pending &&
            job.clips.length === 0 &&
            transcribed && (
            <div className="brief-bar">
              <div>
                <b>你的要求</b>
                <span>{setup.brief}</span>
                <em>
                  {aspect === "source" ? "原画幅" : aspect}
                  {aspect !== "source" && fit === "blur" ? " · 模糊背景" : ""}
                  {subtitles ? " · 烧字幕" : ""}
                  {trimSilence ? " · 去停顿" : ""}
                  {quality === "standard" ? "" : quality === "draft" ? " · 草稿画质" : " · 终版画质"}
                </em>
              </div>
              <div className="brief-actions">
                <button type="button" className="secondary" disabled={busy} onClick={planAuto}>
                  按这个要求选段
                </button>
                <button type="button" className="ghost" onClick={() => setBriefDismissed(true)}>
                  忽略
                </button>
              </div>
            </div>
          )}

          <div className="editor-filters">
            <input
              ref={searchRef}
              className="search"
              type="search"
              placeholder="搜索转写稿…  ( / )"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            {health?.llm_enabled && (
              <input
                className="instruction"
                type="text"
                placeholder="选段要求：例如只挑有争议的观点，每条 20 秒内"
                value={instruction}
                onChange={(event) => setInstruction(event.target.value)}
              />
            )}
            {speakers.length > 0 && (
              <div className="speaker-filter">
                <button
                  type="button"
                  className={speaker === null ? "is-on" : ""}
                  onClick={() => setSpeaker(null)}
                >
                  全部
                </button>
                {speakers.map((name) => (
                  <button
                    type="button"
                    key={name}
                    className={speaker === name ? "is-on" : ""}
                    onClick={() => setSpeaker(speaker === name ? null : name)}
                  >
                    {name}
                  </button>
                ))}
              </div>
            )}
            <a
              className="ghost link"
              href={transcriptSrtUrl(job.id)}
              download="transcript.srt"
            >
              导出 SRT
            </a>
          </div>

          {job.transcript && (
            <TimelineStrip
              duration={job.transcript.duration}
              sentences={job.transcript.sentences}
              selected={selected}
              activeId={activeId}
              time={time}
              onSeek={seek}
            />
          )}

          <TranscriptPane
            transcript={job.transcript ?? null}
            sentences={visibleSentences}
            selected={selected}
            activeId={activeId}
            onToggle={toggleSentence}
            onSeek={seek}
            loading={job.status === "transcribing"}
            loadingLabel="正在转写，请稍候…"
          />
        </section>

        <ChatPanel job={job} />

        <ClipTray
          job={job}
          disabled={busy}
          onRender={render}
          onSeek={seek}
          draftUrl={draftUrl(job.id, aspect)}
        />
      </main>
    </div>
  );
}