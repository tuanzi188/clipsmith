import { useCallback, useEffect, useState } from "react";
import { api, uploadFile } from "./api";
import { ChatHome } from "./ChatHome";
import { Workspace } from "./Workspace";
import { errorMessage } from "./format";
import { DEFAULT_BRIEF, parseBrief } from "./brief";
import type { Health, Job, JobSummary, Setup } from "./types";

const TABS_KEY = "clipsmith.tabs.v2";
const SETUPS_KEY = "clipsmith.setups.v2";
const LEGACY_KEY = "clipsmith.job";
const MAX_TABS = 8;

interface PersistedTabs {
  ids: string[];
  active: string;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function initialTabs(): PersistedTabs {
  const saved = readJson<PersistedTabs>(TABS_KEY, { ids: [], active: "" });
  if (saved.ids.length) return saved;

  const legacy = localStorage.getItem(LEGACY_KEY);
  if (legacy) return { ids: [legacy], active: legacy };
  return { ids: [], active: "" };
}

function syncUrl(jobId: string | null) {
  const url = new URL(window.location.href);
  if (jobId) url.searchParams.set("job", jobId);
  else url.searchParams.delete("job");
  window.history.replaceState({}, "", url);
}

export default function App() {
  const [tabs, setTabs] = useState<PersistedTabs>(initialTabs);
  const [mode, setMode] = useState<"tabs" | "new">(tabs.ids.length ? "tabs" : "new");
  const [health, setHealth] = useState<Health | null>(null);
  const [recent, setRecent] = useState<JobSummary[]>([]);
  const [meta, setMeta] = useState<
    Record<string, { filename: string; status: Job["status"] }>
  >({});
  const [setups, setSetups] = useState<Record<string, Setup>>(
    () => readJson<Record<string, Setup>>(SETUPS_KEY, {}),
  );
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // -- bootstrap ----------------------------------------------------------
  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined);
    api.listJobs().then(setRecent).catch(() => undefined);

    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("job");
    if (fromUrl) {
      setTabs((current) => ({
        ids: current.ids.includes(fromUrl) ? current.ids : [fromUrl, ...current.ids],
        active: fromUrl,
      }));
      setMode("tabs");
    }

    const ids = fromUrl
      ? Array.from(new Set([fromUrl, ...tabs.ids]))
      : tabs.ids;
    for (const id of ids) {
      api
        .getJob(id)
        .then((job) => setMeta((prev) => ({ ...prev, [id]: { filename: job.filename, status: job.status } })))
        .catch(() => {
          setTabs((current) => {
            const next = current.ids.filter((item) => item !== id);
            return {
              ids: next,
              active: current.active === id ? (next[0] ?? "") : current.active,
            };
          });
        });
    }
    localStorage.removeItem(LEGACY_KEY);
  }, []);

  // -- persistence / URL --------------------------------------------------
  useEffect(() => {
    localStorage.setItem(TABS_KEY, JSON.stringify(tabs));
    localStorage.setItem(SETUPS_KEY, JSON.stringify(setups));
  }, [tabs, setups]);

  useEffect(() => {
    if (mode === "tabs" && tabs.active) syncUrl(tabs.active);
    else syncUrl(null);
  }, [mode, tabs.active]);

  useEffect(() => {
    if (mode === "tabs" && !tabs.ids.length) setMode("new");
  }, [mode, tabs.ids.length]);

  // -- tab operations -----------------------------------------------------
  const openJob = useCallback((jobId: string) => {
    setTabs((current) => {
      if (current.ids.includes(jobId)) return { ...current, active: jobId };
      if (current.ids.length >= MAX_TABS) return current;
      return { ids: [jobId, ...current.ids], active: jobId };
    });
    setMode("tabs");
  }, []);

  const closeTab = useCallback((jobId: string) => {
    setTabs((current) => {
      const index = current.ids.indexOf(jobId);
      if (index < 0) return current;
      const ids = current.ids.filter((item) => item !== jobId);
      const active =
        current.active === jobId
          ? (ids[Math.min(index, ids.length - 1)] ?? "")
          : current.active;
      return { ids, active };
    });
  }, []);

  const onMeta = useCallback((job: Job) => {
    setMeta((prev) => ({
      ...prev,
      [job.id]: { filename: job.filename, status: job.status },
    }));
  }, []);

  const refreshRecent = () => {
    api.listJobs().then(setRecent).catch(() => undefined);
  };

  // -- upload from the chat composer --------------------------------------
  const handleUpload = async (file: File, brief: string) => {
    setError(null);
    setUploading(true);
    setProgress(0);
    try {
      const job = await uploadFile(file, setProgress);
      const setup = parseBrief(brief.trim() || DEFAULT_BRIEF);
      setSetups((prev) => ({ ...prev, [job.id]: setup }));
      onMeta(job);
      openJob(job.id);
      refreshRecent();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  const closeAndRefresh = (jobId: string) => {
    closeTab(jobId);
    refreshRecent();
  };

  const tabIds = tabs.ids;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          ClipSmith <span>转写稿剪辑台</span>
        </div>
        <div className="topbar-right">
          {health && (
            <span className="engine-pill">
              {health.engine}
              {health.llm_enabled ? " · AI" : ""}
            </span>
          )}
          <button type="button" className="ghost" onClick={() => setMode("new")}>
            ＋ 新建
          </button>
        </div>
      </header>

      {tabIds.length > 0 && (
        <nav className="tabbar" aria-label="项目标签">
          <div className="tabbar-scroll">
            {tabIds.map((id) => {
              const info = meta[id];
              const status = info?.status ?? "uploaded";
              const isActive = mode === "tabs" && tabs.active === id;
              return (
                <button
                  type="button"
                  key={id}
                  className={`tab${isActive ? " is-active" : ""}`}
                  onClick={() => openJob(id)}
                  title={info?.filename ?? id}
                >
                  <span className={`tab-dot is-${status}`} />
                  <span className="tab-name">{info?.filename ?? "载入中…"}</span>
                  <span
                    className="tab-close"
                    role="button"
                    tabIndex={0}
                    aria-label="关闭标签"
                    onClick={(event) => {
                      event.stopPropagation();
                      closeTab(id);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.stopPropagation();
                        event.preventDefault();
                        closeTab(id);
                      }
                    }}
                  >
                    ×
                  </span>
                </button>
              );
            })}
            {tabIds.length < MAX_TABS && (
              <button
                type="button"
                className="tab-new"
                onClick={() => setMode("new")}
                aria-label="新建任务"
              >
                ＋
              </button>
            )}
          </div>
        </nav>
      )}

      {error && (
        <div className="banner error">
          <span>{error}</span>
          <button type="button" onClick={() => setError(null)}>
            关闭
          </button>
        </div>
      )}

      {mode === "new" && (
        <ChatHome
          recent={recent}
          health={health}
          uploading={uploading}
          progress={progress}
          onOpen={openJob}
          onSubmit={handleUpload}
        />
      )}

      {tabIds.map((id) => (
        <Workspace
          key={id}
          jobId={id}
          health={health}
          visible={mode === "tabs" && tabs.active === id}
          setup={setups[id]}
          onMeta={onMeta}
          onClose={closeAndRefresh}
        />
      ))}
    </div>
  );
}
