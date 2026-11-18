import type { RefObject } from "react";
import { formatTime } from "../format";
import { mediaUrl } from "../api";

interface Props {
  jobId: string;
  videoRef: RefObject<HTMLVideoElement | null>;
  currentTime: number;
  onTimeUpdate: (time: number) => void;
  onLoadedMetadata?: () => void;
  filename: string;
  duration?: number | null;
}

export function VideoStage({
  jobId,
  videoRef,
  currentTime,
  onTimeUpdate,
  onLoadedMetadata,
  filename,
  duration,
}: Props) {
  return (
    <div className="stage">
      <div className="stage-heading">
        <h2 className="studio-panel-title"><span aria-hidden="true">01</span> 原片监看</h2>
        <span className="monitor-light" aria-hidden="true" />
      </div>
      <div className="stage-video">
        <video
          ref={videoRef}
          src={mediaUrl(jobId)}
          controls
          preload="metadata"
          playsInline
          onTimeUpdate={(event) => onTimeUpdate(event.currentTarget.currentTime)}
          onSeeked={(event) => onTimeUpdate(event.currentTarget.currentTime)}
          onLoadedMetadata={() => onLoadedMetadata?.()}
        />
      </div>
      <div className="stage-meta">
        <div className="stage-name" title={filename}>
          {filename}
        </div>
        <div className="stage-time">
          {formatTime(currentTime, true)}
          <span> / {formatTime(duration ?? 0)}</span>
        </div>
      </div>
    </div>
  );
}
