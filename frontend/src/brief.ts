import type { Aspect, Setup } from "./types";

/**
 * Used when the user drops a file and clicks 剪辑 without typing anything.
 * Uploads should not dead-end at a transcript screen: the product promise is
 * that a fresh clip arrives without a prompt.
 */
export const DEFAULT_BRIEF =
  "先剪一版：挑出最完整、最有吸引力的片段，竖屏 9:16，去掉停顿，加字幕；素材少就出一条，别硬凑。";

/**
 * Cheap intent extraction from the chat brief. We only take the settings that
 * are unambiguous ("竖屏" / "加字幕" / "去掉停顿"); everything else stays as the
 * free-form instruction handed to the LLM, so no keyword is silently dropped.
 */
export function parseBrief(brief: string): Setup {
  const text = brief.trim();
  let aspect: Aspect = "source";
  if (/竖屏|9\s*[:：]\s*16|抖音|快手|shorts|reels/i.test(text)) aspect = "9:16";
  else if (/方形|1\s*[:：]\s*1|square/i.test(text)) aspect = "1:1";

  const subtitles = /字幕/.test(text) && !/不要字幕|无字幕/.test(text);
  const trim = !/不去停顿|保留停顿|不要去掉停顿|保留语气/.test(text);
  return { brief: text, aspect, subtitles, trim };
}
