import type { Sentence } from "../types";
import { formatTime } from "../format";

interface Props {
  duration: number;
  sentences: Sentence[];
  selected: Set<number>;
  activeId: number | null;
  time: number;
  onSeek: (seconds: number) => void;
}

/**
 * 以真实句子时间为边界，显示选区与当前播放位置。
 */
export function TimelineStrip({ duration, sentences, selected, activeId, time, onSeek }: Props) {
  if (!duration || sentences.length === 0) return null;
  const total = Math.max(duration, 0.1);
  const progress = Math.min(100, Math.max(0, (time / total) * 100));

  return (
    <section className="studio-timeline">
      <div className="studio-timeline-head">
        <h2 className="studio-panel-title"><span aria-hidden="true">04</span> 素材时间轴</h2>
        <span className="timeline-readout">{formatTime(time, true)} <span>/ {formatTime(total)}</span></span>
      </div>
      <div className="timeline-ruler" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <span key={fraction}>{formatTime(total * fraction)}</span>
        ))}
      </div>
    <div
      className="timeline"
      role="slider"
      aria-label="转写稿时间条"
      aria-valuemin={0}
      aria-valuemax={Math.round(total)}
      aria-valuenow={Math.round(time)}
      tabIndex={-1}
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect();
        const ratio = (event.clientX - rect.left) / Math.max(1, rect.width);
        onSeek(Math.max(0, Math.min(total, ratio * total)));
      }}
    >
      <div className="timeline-track">
        {sentences.map((sentence) => {
          const left = (sentence.start / total) * 100;
          const width = Math.max(0.35, ((sentence.end - sentence.start) / total) * 100);
          const className = [
            "timeline-block",
            selected.has(sentence.id) ? "is-selected" : "",
            activeId === sentence.id ? "is-active" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <span
              key={sentence.id}
              className={className}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`${formatTime(sentence.start)} · ${sentence.text}`}
            />
          );
        })}
      </div>
      <span className="timeline-playhead" style={{ left: `${progress}%` }} />
    </div>
    </section>
  );
}
