import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { errorMessage } from "../format";
import type { ChatMessage, Job } from "../types";

interface Props {
  job: Job;
}

const QUICK = ["再短一点", "去掉停顿", "加字幕", "换成竖屏", "模糊背景", "终版画质", "导出成片"];

export function ChatPanel({ job }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const pending = job.chat_pending;
  const blocked = job.status === "transcribing";

  const refresh = useCallback(async () => {
    const state = await api.getChat(job.id);
    setMessages(state.messages);
  }, [job.id]);

  useEffect(() => {
    setMessages([]);
    setError(null);
    refresh().catch(() => undefined);
  }, [refresh]);

  useEffect(() => {
    if (!pending) return;
    const timer = window.setInterval(() => {
      refresh().catch(() => undefined);
    }, 1200);
    return () => window.clearInterval(timer);
  }, [pending, refresh]);

  useEffect(() => {
    if (pending) return;
    refresh().catch(() => undefined);
  }, [pending, refresh]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, pending]);

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || sending || pending || blocked) return;
    setSending(true);
    setError(null);
    try {
      await api.sendChat(job.id, message);
      setInput("");
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="chat-panel">
      <header className="chat-panel-head">
        <div>
          <h2>剪辑导演</h2>
          <span>{pending ? "正在执行…" : "说一句，我来剪"}</span>
        </div>
        <span className={`chat-live${pending ? " is-busy" : ""}`} />
      </header>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>直接说你想怎么剪。</p>
            <span>例如：剪成 3 条竖屏，去掉停顿，加字幕</span>
            <span>片段生成后还可以说：第二条再短一点 / 换成 1:1</span>
          </div>
        )}

        {messages.map((message, index) => (
          <div key={`${message.at}-${index}`} className={`msg is-${message.role}`}>
            {message.role === "assistant" && <span className="msg-avatar">AI</span>}
            <div className="msg-body">
              <p>{message.content}</p>
              {message.actions && message.actions.length > 0 && (
                <div className="receipts">
                  {message.actions.map((action, actionIndex) => (
                    <span key={`${action.tool}-${actionIndex}`} className="receipt">
                      ✓ {action.summary}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {pending && (
          <div className="msg is-assistant is-working">
            <span className="msg-avatar">AI</span>
            <div className="msg-body">
              <p>
                {job.message || "正在处理"}
                <span className="dots" />
              </p>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {error && <div className="chat-error">{error}</div>}

      <div className="chat-quick">
        {QUICK.map((item) => (
          <button
            type="button"
            key={item}
            disabled={pending || blocked || sending}
            onClick={() => void send(item)}
          >
            {item}
          </button>
        ))}
      </div>

      <div className="chat-composer">
        <textarea
          rows={2}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send(input);
            }
          }}
          placeholder={
            blocked ? "转写完成后就可以对话剪辑…" : "继续说：把第二条压到 15 秒…"
          }
          disabled={blocked}
        />
        <button
          type="button"
          className="primary"
          disabled={pending || blocked || sending || !input.trim()}
          onClick={() => void send(input)}
        >
          发送
        </button>
      </div>
      <p className="chat-foot">
        每一步都会落到项目里；破坏性操作会先问你。
      </p>
    </section>
  );
}