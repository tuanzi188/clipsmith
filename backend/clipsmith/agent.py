"""Conversational editing agent.

The model never touches media directly. It can only call the tools below, and
every tool validates its input against the transcript/project before mutating
anything. That keeps "chat to edit" honest: no invented timestamps, no silent
overwrites, and every step is visible in the chat as a receipt.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from openai import OpenAI

from .config import Settings
from .draft_export import canvas_for, export_draft
from .engine.base import Engine
from .jobs import JobStore
from .media import detect_silences, probe
from .models import Job, now_iso
from .planner import (
    PlanOptions,
    apply_silence_trim,
    clips_from_specs,
)
from .render import render_selected
from .voiceover import VOICES, add_voiceover_to_clip

MAX_STEPS = 10
MAX_HISTORY = 12

Publish = Callable[[Job], Awaitable[None]]


# -- chat persistence ------------------------------------------------------


def load_chat(store: JobStore, job_id: str) -> list[dict]:
    path = store.chat_path(job_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_chat(store: JobStore, job_id: str, messages: list[dict]) -> None:
    path = store.chat_path(job_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def append_chat(
    store: JobStore,
    job_id: str,
    role: str,
    content: str,
    actions: list[dict] | None = None,
) -> dict:
    messages = load_chat(store, job_id)
    message = {
        "role": role,
        "content": content,
        "at": now_iso(),
        "actions": actions or [],
    }
    messages.append(message)
    save_chat(store, job_id, messages)
    return message


# -- tool schemas ----------------------------------------------------------

_TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_project_state",
            "description": "查看项目现状：素材时长、是否已转写、已有片段和导出设置。每轮开始先调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transcript",
            "description": "读取转写稿。可以分页，也可以按关键词搜索。返回句子 id、时间、文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 60},
                    "query": {"type": "string", "description": "关键词，留空则按位置分页"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe",
            "description": "对素材做语音转写。已经转写过时直接返回现状，不要重复调用。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_export_options",
            "description": "设置导出参数：画幅、竖屏适配方式、是否烧字幕、成片画质。",
            "parameters": {
                "type": "object",
                "properties": {
                    "aspect": {"type": "string", "enum": ["source", "9:16", "1:1"]},
                    "fit": {
                        "type": "string",
                        "enum": ["crop", "blur"],
                        "description": "竖屏/方形时：crop 居中裁切，blur 保留全画面+模糊背景",
                    },
                    "subtitles": {"type": "boolean"},
                    "quality": {
                        "type": "string",
                        "enum": ["draft", "standard", "final"],
                        "description": "draft 快速草稿，standard 默认交付，final 高码率终版",
                    },
                    "trim_silence": {"type": "boolean", "description": "选段时是否去掉停顿"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_clips",
            "description": "用句子 id 区间设置候选片段（会替换现有片段）。只能使用转写稿里真实存在的句子 id。按吸引力排序，通常 3~6 个；后端硬上限 12 个，超出会被截断。",
            "parameters": {
                "type": "object",
                "properties": {
                    "clips": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from_sentence": {"type": "integer"},
                                "to_sentence": {"type": "integer"},
                                "title": {"type": "string"},
                                "hook": {"type": "string"},
                                "caption": {"type": "string"},
                                "tags": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["from_sentence", "to_sentence"],
                        },
                    },
                    "trim_silence": {"type": "boolean"},
                },
                "required": ["clips"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_clip",
            "description": "修改单个片段的句子范围或文案，用于“第二条再短一点”这类微调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "from_sentence": {"type": "integer"},
                    "to_sentence": {"type": "integer"},
                    "title": {"type": "string"},
                    "caption": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["clip_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_clips",
            "description": "渲染片段成 mp4（按当前画幅/字幕设置）。用户要求“出片/导出”时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "留空表示渲染全部待导出片段",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_voiceover",
            "description": (
                "给已导出的片段加一条独立旁白：旁白叠在原声上，原声自动压低，"
                "不替换原声。用户要求配解说/旁白/串词时调用。文案要自己先写好，"
                "中文 1-2 句、不超过 80 字。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_id": {"type": "string"},
                    "text": {
                        "type": "string",
                        "description": "旁白文案，中文 1-2 句，不超过 80 字",
                    },
                    "voice": {
                        "type": "string",
                        "enum": VOICES,
                        "description": "音色，默认女声 Xiaoxiao",
                    },
                },
                "required": ["clip_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_clips",
            "description": "删除片段。属于破坏性操作，仅在用户明确要求时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "clip_ids": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["clip_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_draft",
            "description": "生成剪映草稿（视频轨 + 字幕轨），返回下载入口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "aspect": {"type": "string", "enum": ["source", "9:16", "1:1"]}
                },
                "required": [],
            },
        },
    },
]

_SYSTEM = """你是 ClipSmith 的 AI 剪辑导演，正在一个本地剪辑工作台里工作。
你不是在聊天，你要直接调用工具把片子剪出来。

工作规则：
1. 每轮先调用 get_project_state 了解现状。
2. 需要内容时调用 get_transcript（可搜索/分页）。选段只能使用真实存在的句子 id，绝不能编造时间码。
3. 每个片段必须是连续句子，时长约 8~60 秒，最多 6 个片段，按吸引力排序。
4. 用户说“剪成/出片/导出/要 3 条”时：select_clips 之后必须调用 render_clips 产出成片；用户只是“看看/推荐”时不要渲染。
5. 用户提到竖屏/方形/加字幕/去停顿/模糊背景/画质，就调用 set_export_options；用户要求“第二条再短一点”，用 update_clip 调整句子范围。竖屏裁切会切掉画面两侧，用户嫌“人出画/别裁”时改用 fit=blur。
6. 已经转写过就不要重复调用 transcribe。工具失败时换一种做法，不要重复同一个失败调用。
7. 删除片段属于破坏性操作，用户明确要求才做。
8. 每轮最多调用 4 个工具；不要一次做完所有事，必要时分步。
9. 回复用中文，简短说明：做了什么 → 片段清单（标题 + 时长）→ 一句下一步建议。不要输出 JSON、代码块或工具名列表。
10. 不要声称做了没有通过工具真正执行的事。
11. 用户要旁白/解说/串词时：自己写 1–2 句中文（不超过 80 字），片段必须已经导出了，再调用 add_voiceover。旁白叠在原声上、原声会被压低，不会替换原声，回复里要说清楚这一点。
"""


class ClipAgent:
    def __init__(
        self,
        store: JobStore,
        job_id: str,
        settings: Settings,
        engine: Engine,
        publish: Publish,
    ) -> None:
        self.store = store
        self.job_id = job_id
        self.settings = settings
        self.engine = engine
        self.publish = publish
        self.job: Job = store.load(job_id)

    async def run(self) -> None:
        job = self.job
        history = load_chat(self.store, self.job_id)
        conversation = [{"role": "system", "content": self._system_prompt(job)}]
        for message in history[-MAX_HISTORY:]:
            if message["role"] in ("user", "assistant"):
                conversation.append(
                    {"role": message["role"], "content": message["content"]}
                )

        actions: list[dict] = []
        reply = ""
        try:
            for _ in range(MAX_STEPS):
                response = await asyncio.to_thread(self._call_llm, conversation)
                choice = response.choices[0]
                message = choice.message
                if message.tool_calls:
                    conversation.append(
                        {
                            "role": "assistant",
                            "content": message.content or "",
                            "tool_calls": [
                                {
                                    "id": call.id,
                                    "type": "function",
                                    "function": {
                                        "name": call.function.name,
                                        "arguments": call.function.arguments,
                                    },
                                }
                                for call in message.tool_calls
                            ],
                        }
                    )
                    for call in message.tool_calls:
                        try:
                            arguments = json.loads(call.function.arguments or "{}")
                        except json.JSONDecodeError:
                            arguments = {}
                        payload, summary = await asyncio.to_thread(
                            self._execute, call.function.name, arguments
                        )
                        actions.append(
                            {"tool": call.function.name, "summary": summary}
                        )
                        conversation.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": json.dumps(
                                    payload, ensure_ascii=False
                                )[:12000],
                            }
                        )
                        self.job = self.store.load(self.job_id)
                        await self.publish(self.job)
                    continue
                reply = (message.content or "").strip()
                break
        except Exception as exc:  # noqa: BLE001 - reported in chat
            reply = f"这一步没做成：{type(exc).__name__}: {exc}"

        if not reply:
            reply = "这轮先停在这里，你可以继续说下一步要改什么。"

        try:
            append_chat(self.store, self.job_id, "assistant", reply, actions)
        finally:
            # chat_pending must be cleared even if persistence blows up, or the
            # job is stuck on "上一条指令还在执行" forever.
            try:
                job = self.store.load(self.job_id)
                job.chat_pending = False
                self.store.save(job)
                await self.publish(job)
            except Exception:  # noqa: BLE001 - nothing left to recover onto
                pass

    # -- LLM ---------------------------------------------------------------
    def _call_llm(self, messages: list[dict]) -> Any:
        client = OpenAI(
            api_key=self.settings.llm_api_key or "local",
            base_url=self.settings.llm_base_url or None,
            timeout=self.settings.llm_timeout,
        )
        return client.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            tools=_TOOL_SPECS,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=4000,
        )

    def _system_prompt(self, job: Job) -> str:
        speech = (
            f"已转写 {len(job.transcript.sentences)} 句"
            if job.transcript
            else "尚未转写"
        )
        return (
            _SYSTEM
            + f"\n当前项目：{job.filename}，时长 {job.duration or 0:.0f} 秒，"
            + f"状态 {job.status}，{speech}，已有 {len(job.clips)} 个片段。"
        )

    # -- tool dispatch -----------------------------------------------------
    def _execute(self, name: str, arguments: dict) -> tuple[dict, str]:
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return {"ok": False, "error": f"unknown tool: {name}"}, f"{name}: 未知工具"
            signature = inspect.signature(handler)
            accepted = {k: v for k, v in arguments.items() if k in signature.parameters}
            result = handler(**accepted)
            return result, str(result.get("summary") or name)
        except Exception as exc:  # noqa: BLE001 - returned to the model
            return (
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                f"{name}: 失败（{exc}）",
            )

    # -- tools -------------------------------------------------------------
    def _tool_get_project_state(self) -> dict:
        job = self.job
        clips = []
        for clip in job.clips:
            ids = clip.sentence_ids or []
            clips.append(
                {
                    "id": clip.id,
                    "title": clip.title or clip.hook,
                    "from_sentence": min(ids) if ids else None,
                    "to_sentence": max(ids) if ids else None,
                    "start": round(clip.start, 2),
                    "end": round(clip.end, 2),
                    "duration": round(clip.duration, 1),
                    "status": clip.status,
                }
            )
        transcript = None
        if job.transcript:
            speakers = sorted(
                {s.speaker for s in job.transcript.sentences if s.speaker}
            )
            transcript = {
                "sentences": len(job.transcript.sentences),
                "engine": job.transcript.engine,
                "speakers": speakers,
            }
        return {
            "ok": True,
            "filename": job.filename,
            "duration": job.duration,
            "status": job.status,
            "transcript": transcript,
            "clips": clips,
            "export": {
                "aspect": job.export_aspect,
                "fit": job.aspect_fit,
                "subtitles": job.export_subtitles,
                "quality": job.render_quality,
                "trim_silence": job.trim_silence,
            },
            "summary": f"{job.filename}，{len(clips)} 个片段，{job.status}",
        }

    def _tool_get_transcript(
        self,
        offset: int = 0,
        limit: int = 30,
        query: str | None = None,
    ) -> dict:
        job = self.job
        if not job.transcript:
            return {"ok": False, "error": "尚未转写，请先调用 transcribe"}
        sentences = job.transcript.sentences
        if query:
            needle = query.lower()
            matched = [s for s in sentences if needle in s.text.lower()]
            total = len(matched)
            page = matched[offset : offset + min(limit, 60)]
        else:
            total = len(sentences)
            page = sentences[offset : offset + min(limit, 60)]

        rows = [
            {
                "id": s.id,
                "start": round(s.start, 1),
                "end": round(s.end, 1),
                "speaker": s.speaker,
                "text": s.text,
            }
            for s in page
        ]
        return {
            "ok": True,
            "total": total,
            "offset": offset,
            "sentences": rows,
            "summary": f"句子 {offset}~{offset + len(rows)} / 共 {total} 条",
        }

    def _tool_transcribe(self) -> dict:
        job = self.job
        if job.transcript:
            return {
                "ok": True,
                "already": True,
                "sentences": len(job.transcript.sentences),
                "summary": f"已经转写过（{len(job.transcript.sentences)} 句）",
            }
        if job.status == "transcribing":
            return {"ok": False, "error": "正在转写中，稍后查看项目状态"}
        media = self.store.media_path(job.id, job.media_file)
        job.status = "transcribing"
        job.progress = 10
        self.store.save(job)
        transcript = self.engine.transcribe(media, self.store.job_dir(job.id))
        job = self.store.load(job.id)
        job.transcript = transcript
        job.duration = round(transcript.duration, 3)
        job.status = "transcribed"
        job.progress = 100
        job.message = f"{len(transcript.sentences)} 句 · {transcript.engine}"
        self.store.save(job)
        return {
            "ok": True,
            "sentences": len(transcript.sentences),
            "duration": job.duration,
            "summary": f"转写完成：{len(transcript.sentences)} 句",
        }

    def _tool_set_export_options(
        self,
        aspect: str | None = None,
        fit: str | None = None,
        subtitles: bool | None = None,
        quality: str | None = None,
        trim_silence: bool | None = None,
    ) -> dict:
        job = self.job
        if aspect is not None:
            if aspect not in ("source", "9:16", "1:1"):
                return {"ok": False, "error": f"不支持的画幅：{aspect}"}
            job.export_aspect = aspect
        if fit is not None:
            if fit not in ("crop", "blur"):
                return {"ok": False, "error": f"不支持的适配方式：{fit}"}
            job.aspect_fit = fit
        if subtitles is not None:
            job.export_subtitles = bool(subtitles)
        if quality is not None:
            if quality not in ("draft", "standard", "final"):
                return {"ok": False, "error": f"不支持的画质档位：{quality}"}
            job.render_quality = quality
        if trim_silence is not None:
            job.trim_silence = bool(trim_silence)
        self.store.save(job)
        return {
            "ok": True,
            "export": {
                "aspect": job.export_aspect,
                "fit": job.aspect_fit,
                "subtitles": job.export_subtitles,
                "quality": job.render_quality,
                "trim_silence": job.trim_silence,
            },
            "summary": (
                f"画幅 {job.export_aspect}（{job.aspect_fit}），"
                f"字幕{'开' if job.export_subtitles else '关'}，"
                f"画质 {job.render_quality}，"
                f"去停顿{'开' if job.trim_silence else '关'}"
            ),
        }

    def _tool_select_clips(
        self,
        clips: list[dict] | None = None,
        trim_silence: bool | None = None,
    ) -> dict:
        job = self.job
        if not job.transcript:
            return {"ok": False, "error": "尚未转写，请先调用 transcribe"}
        if not clips:
            return {"ok": False, "error": "clips 不能为空"}

        options = PlanOptions(
            mode="manual",
            limit=max(1, len(clips)),
            min_seconds=self.settings.min_clip_seconds,
            max_seconds=self.settings.max_clip_seconds,
            trim_silence=(
                job.trim_silence if trim_silence is None else bool(trim_silence)
            ),
        )
        built = clips_from_specs(job.transcript, clips, options)
        if not built:
            return {"ok": False, "error": "没有任何有效的句子区间"}

        if options.trim_silence:
            try:
                media = self.store.media_path(job.id, job.media_file)
                apply_silence_trim(
                    built, detect_silences(media), options
                )
            except Exception:  # noqa: BLE001 - trimming is best effort
                pass

        job.clips = built
        job.status = "planned"
        job.progress = 100
        job.message = f"AI 选了 {len(built)} 个片段"
        job.error = None
        self.store.save(job)
        self.store.cleanup_clips(job)
        return {
            "ok": True,
            "clips": [
                {
                    "id": clip.id,
                    "title": clip.title or clip.hook,
                    "from_sentence": min(clip.sentence_ids),
                    "to_sentence": max(clip.sentence_ids),
                    "start": round(clip.start, 1),
                    "end": round(clip.end, 1),
                    "duration": round(clip.end - clip.start, 1),
                }
                for clip in built
            ],
            "summary": f"生成 {len(built)} 个片段：" + "、".join(
                f"{clip.title or clip.hook}（{clip.end - clip.start:.0f}s）"
                for clip in built[:4]
            ),
        }

    def _tool_update_clip(
        self,
        clip_id: str,
        from_sentence: int | None = None,
        to_sentence: int | None = None,
        title: str | None = None,
        caption: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        job = self.job
        clip = next((c for c in job.clips if c.id == clip_id), None)
        if not clip:
            return {"ok": False, "error": f"找不到片段 {clip_id}"}
        if not job.transcript:
            return {"ok": False, "error": "尚未转写"}

        if from_sentence is not None or to_sentence is not None:
            by_id = {s.id: s for s in job.transcript.sentences}
            first = from_sentence if from_sentence is not None else min(clip.sentence_ids)
            last = to_sentence if to_sentence is not None else max(clip.sentence_ids)
            if first > last:
                first, last = last, first
            ids = [i for i in range(first, last + 1) if i in by_id]
            if not ids:
                return {"ok": False, "error": "句子区间无效"}
            clip.sentence_ids = ids
            clip.start = by_id[ids[0]].start
            clip.end = by_id[ids[-1]].end
            clip.ranges = []
            clip.file = None
            clip.status = "planned"
            clip.removed_pauses = 0
            clip.removed_seconds = 0.0
            # The old render and its narration no longer match the new range.
            self.store.base_clip_path(job.id, clip.id).unlink(missing_ok=True)
            clip.voiceover_text = ""

        if title is not None:
            clip.title = title[:40]
        if caption is not None:
            clip.caption = caption[:120]
        if tags is not None:
            clip.tags = [str(t).lstrip("#")[:16] for t in tags][:4]
        self.store.save(job)
        return {
            "ok": True,
            "clip": {
                "id": clip.id,
                "title": clip.title,
                "start": round(clip.start, 1),
                "end": round(clip.end, 1),
            },
            "summary": f"已调整 {clip.title or clip.id} 为 {clip.end - clip.start:.0f} 秒",
        }

    def _tool_render_clips(self, clip_ids: list[str] | None = None) -> dict:
        job = self.job
        if not job.clips:
            return {"ok": False, "error": "还没有片段，先调用 select_clips"}
        targets = clip_ids
        if targets is None:
            targets = [c.id for c in job.clips if c.status in ("planned", "error")]
        if not targets:
            targets = [c.id for c in job.clips]
        rendered = render_selected(
            self.store,
            job,
            targets,
            aspect=job.export_aspect,
            subtitles=job.export_subtitles,
        )
        self.job = self.store.load(self.job_id)
        done = [c for c in rendered.clips if c.id in set(targets)]
        ready = [c for c in done if c.status == "ready"]
        return {
            "ok": bool(ready),
            "ready": len(ready),
            "failed": len(done) - len(ready),
            "clips": [
                {
                    "id": c.id,
                    "title": c.title or c.hook,
                    "status": c.status,
                    "file": c.file,
                }
                for c in done
            ],
            "summary": f"导出完成 {len(ready)}/{len(done)} 个片段"
            + ("（已烧字幕）" if job.export_subtitles else "")
            + f"，画质 {job.render_quality}",
        }

    def _tool_add_voiceover(
        self,
        clip_id: str,
        text: str,
        voice: str | None = None,
    ) -> dict:
        job = self.job
        clip = next((item for item in job.clips if item.id == clip_id), None)
        if clip is None:
            return {"ok": False, "error": f"找不到片段 {clip_id}"}
        if clip.status != "ready" or not clip.file:
            return {"ok": False, "error": "这个片段还没有导出，先调用 render_clips"}
        if not text.strip():
            return {"ok": False, "error": "旁白文案不能为空"}
        if voice and voice not in VOICES:
            return {"ok": False, "error": f"不支持的音色：{voice}"}
        try:
            result = add_voiceover_to_clip(
                self.store, job, clip_id, text.strip()[:200], voice=voice
            )
        except Exception as exc:  # noqa: BLE001 - returned to the model
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.job = self.store.load(self.job_id)
        return {
            "ok": True,
            "clip_id": clip_id,
            **result,
            "summary": (
                f"已给「{clip.title or clip.hook}」加旁白"
                f"（{text.strip()[:24]}…），原声压低、未替换"
            ),
        }

    def _tool_delete_clips(self, clip_ids: list[str]) -> dict:
        job = self.job
        before = len(job.clips)
        remove = set(clip_ids or [])
        job.clips = [c for c in job.clips if c.id not in remove]
        job.status = "planned" if job.clips else "transcribed"
        self.store.save(job)
        self.store.cleanup_clips(job)
        return {
            "ok": True,
            "removed": before - len(job.clips),
            "summary": f"删除 {before - len(job.clips)} 个片段",
        }

    def _tool_export_draft(self, aspect: str | None = None) -> dict:
        job = self.job
        if not job.clips:
            return {"ok": False, "error": "还没有片段"}
        if not job.transcript:
            return {"ok": False, "error": "尚未转写"}
        source = self.store.media_path(job.id, job.media_file)
        info = probe(source)
        width, height = canvas_for(aspect or job.export_aspect, info)
        export_draft(
            source,
            job.transcript.sentences,
            sorted(job.clips, key=lambda c: c.start),
            self.store.job_dir(job.id) / "drafts",
            name=f"clipsmith-{job.id}",
            width=width,
            height=height,
        )
        return {
            "ok": True,
            "download": f"/api/jobs/{job.id}/export/draft?aspect={aspect or job.export_aspect}",
            "summary": f"剪映草稿已生成（{width}×{height}），可在片段区下载",
        }