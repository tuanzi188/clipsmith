import type { Sentence, Transcript } from "../types";
import { formatTime } from "../format";

interface Props {
  transcript: Transcript | null;
  sentences: Sentence[];
  selected: Set<number>;
  activeId: number | null;
  onToggle: (id: number, shiftKey: boolean) => void;
  onSeek: (seconds: number) => void;
  loading: boolean;
  loadingLabel: string;
}

export function TranscriptPane({
  transcript,
  sentences,
  selected,
  activeId,
  onToggle,
  onSeek,
  loading,
  loadingLabel,
}: Props) {
  if (!transcript) {
    return (
      <div className="transcript empty">
        {loading ? (
          <>
            <div className="pulse" />
            <p>{loadingLabel}</p>
            <span>首次转写需要加载模型，之后会快很多。</span>
          </>
        ) : (
          <>
            <p>还没有转写稿</p>
            <span>上传素材后会自动开始转写。</span>
          </>
        )}
      </div>
    );
  }

  if (sentences.length === 0) {
    return (
      <div className="transcript empty">
        <p>没有匹配的句子</p>
        <span>换个关键词，或清空说话人筛选。</span>
      </div>
    );
  }

  return (
    <div className="transcript">
      <ol className="sentence-list">
        {sentences.map((sentence) => {
          const isSelected = selected.has(sentence.id);
          const isActive = activeId === sentence.id;
          return (
            <li key={sentence.id}>
              <button
                type="button"
                aria-pressed={isSelected}
                className={`sentence${isSelected ? " is-selected" : ""}${
                  isActive ? " is-active" : ""
                }`}
                onClick={(event) => {
                  onToggle(sentence.id, event.shiftKey);
                  onSeek(sentence.start);
                }}
              >
                <span className="sentence-time">{formatTime(sentence.start)}</span>
                <span className="sentence-body">
                  {sentence.speaker && (
                    <span className="speaker">{sentence.speaker}</span>
                  )}
                  {sentence.text}
                </span>
                <span className="sentence-check" aria-hidden>
                  {isSelected ? "✓" : ""}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
