"""FastAPI app: upload -> transcribe -> plan -> render, plus an SSE job stream."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import ClipAgent, append_chat, load_chat
from .config import Settings, settings
from .engine import build_engine
from .draft_export import canvas_for, export_draft, zip_draft
from .events import bus
from .jobs import JobStore
from .media import MediaError, burn_subtitles, detect_silences, probe, render_ranges
from .models import Job
from .planner import LLMConfig, PlanOptions, apply_silence_trim, clips_from_sentences, plan
from .render import render_selected
from .subtitles import clip_srt, to_srt
from .voiceover import add_voiceover_to_clip

ALLOWED_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi",
    ".wav", ".mp3", ".m4a", ".flac", ".ogg",
}
CHUNK_SIZE = 4 * 1024 * 1024
BUSY_STATES = {"transcribing", "planning", "rendering"}


class PlanRequest(BaseModel):
    mode: Literal["rules", "llm", "manual"] = "rules"
    sentence_ids: list[int] | None = None
    limit: int = Field(default=8, ge=1, le=30)
    min_seconds: float | None = Field(default=None, ge=3, le=180)
    max_seconds: float | None = Field(default=None, ge=5, le=300)
    trim_silence: bool = True
    instruction: str | None = Field(default=None, max_length=500)


class RenderRequest(BaseModel):
    clip_ids: list[str] | None = None
    aspect: Literal["source", "9:16", "1:1"] | None = None
    fit: Literal["crop", "blur"] | None = None
    subtitles: bool | None = None
    subtitle_style: Literal["clean", "bold"] = "clean"
    quality: Literal["draft", "standard", "final"] | None = None


class OptionsRequest(BaseModel):
    aspect: Literal["source", "9:16", "1:1"] | None = None
    fit: Literal["crop", "blur"] | None = None
    subtitles: bool | None = None
    trim_silence: bool | None = None
    quality: Literal["draft", "standard", "final"] | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    aspect: Literal["source", "9:16", "1:1"] | None = None
    fit: Literal["crop", "blur"] | None = None
    subtitles: bool | None = None
    trim_silence: bool | None = None
    quality: Literal["draft", "standard", "final"] | None = None


class VoiceoverRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    voice: str | None = None


def _repair_filename(name: str) -> str:
    """multipart headers are latin-1 by spec; browsers send UTF-8 bytes."""
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def create_app(config: Settings | None = None) -> FastAPI:
    cfg = config or settings
    app = FastAPI(title="ClipSmith", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                       "http://localhost:4173", "http://127.0.0.1:4173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = JobStore(cfg.data_dir)
    app.state.engine = build_engine(cfg)
    app.state.busy: dict[str, str] = {}
    app.state.tasks: set[asyncio.Task] = set()

    store: JobStore = app.state.store
    engine = app.state.engine

    # -- helpers -----------------------------------------------------------
    def load_job(job_id: str) -> Job:
        try:
            return store.load(job_id)
        except FileNotFoundError:
            raise HTTPException(404, "job not found") from None

    async def publish(job: Job) -> None:
        await bus.publish(job.id, {"job": job.to_dict()})

    def spawn(coro) -> None:
        task = asyncio.create_task(coro)
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)

    def claim(job_id: str, action: str) -> bool:
        if job_id in app.state.busy:
            return False
        app.state.busy[job_id] = action
        return True

    # -- routes ------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict:
        return {
            "ok": True,
            "engine": engine.name,
            "llm_enabled": cfg.llm_enabled,
            "data_dir": str(cfg.data_dir),
        }

    @app.get("/api/jobs")
    async def list_jobs(limit: int = 20) -> list[dict]:
        items = []
        for job in store.list_recent(limit):
            items.append(
                {
                    "id": job.id,
                    "filename": job.filename,
                    "status": job.status,
                    "duration": job.duration,
                    "created_at": job.created_at,
                    "clips": len([c for c in job.clips if c.status == "ready"]),
                }
            )
        return items

    @app.post("/api/jobs")
    async def upload(file: UploadFile = File(...)) -> dict:
        original = _repair_filename(file.filename or "upload")
        suffix = Path(original).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(400, f"unsupported file type: {suffix or '(none)'}")

        job = store.create(original)
        dest = store.job_dir(job.id) / f"source{suffix}"
        limit = cfg.max_upload_mb * 1024 * 1024
        written = 0
        try:
            with dest.open("wb") as handle:
                while chunk := await file.read(CHUNK_SIZE):
                    written += len(chunk)
                    if written > limit:
                        raise HTTPException(413, f"file exceeds {cfg.max_upload_mb} MB")
                    handle.write(chunk)
            info = await asyncio.to_thread(probe, dest)
            if not info.has_audio:
                raise HTTPException(400, "no audio track; nothing to transcribe")
            job.media_file = dest.name
            job.duration = round(info.duration, 3)
            job.has_audio = info.has_audio
            job.message = f"{info.duration:.0f}s · {written / 1024 / 1024:.1f} MB"
            store.save(job)
        except MediaError as exc:
            shutil.rmtree(store.job_dir(job.id), ignore_errors=True)
            raise HTTPException(400, f"文件无法解析：{exc}") from exc
        except Exception:
            shutil.rmtree(store.job_dir(job.id), ignore_errors=True)
            raise
        await publish(job)
        return job.to_dict()

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        return load_job(job_id).to_dict()

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str) -> dict:
        load_job(job_id)
        if job_id in app.state.busy:
            raise HTTPException(409, "job is busy")
        store.delete(job_id)
        return {"deleted": job_id}

    @app.get("/api/jobs/{job_id}/media")
    async def get_media(job_id: str) -> FileResponse:
        job = load_job(job_id)
        path = store.media_path(job_id, job.media_file)
        if not path.exists():
            raise HTTPException(404, "media missing")
        return FileResponse(path)

    @app.get("/api/jobs/{job_id}/clips/{clip_id}/file")
    async def get_clip_file(job_id: str, clip_id: str) -> FileResponse:
        job = load_job(job_id)
        clip = next((c for c in job.clips if c.id == clip_id), None)
        if not clip or not clip.file:
            raise HTTPException(404, "clip not rendered")
        path = store.job_dir(job_id) / clip.file
        if not path.exists():
            raise HTTPException(404, "clip file missing")
        return FileResponse(path)

    # -- pipeline ----------------------------------------------------------
    @app.post("/api/jobs/{job_id}/transcribe")
    async def transcribe(job_id: str) -> dict:
        job = load_job(job_id)
        if job.chat_pending:
            raise HTTPException(409, "AI 正在执行对话操作")
        if not claim(job_id, "transcribe"):
            raise HTTPException(409, "job is busy")
        job.status = "transcribing"
        job.progress = 5
        job.error = None
        job.message = f"转写中 · {engine.name}"
        store.save(job)
        await publish(job)
        spawn(_transcribe(app, job_id))
        return job.to_dict()

    async def _transcribe(app: FastAPI, job_id: str) -> None:
        store: JobStore = app.state.store
        engine = app.state.engine
        job = store.load(job_id)
        media = store.media_path(job_id, job.media_file)
        try:
            transcript = await asyncio.to_thread(engine.transcribe, media, store.job_dir(job_id))
            job.transcript = transcript
            job.duration = round(transcript.duration, 3)
            job.status = "transcribed"
            job.progress = 100
            job.message = f"{len(transcript.sentences)} 句 · {engine.name}"
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = "转写失败"
        finally:
            app.state.busy.pop(job_id, None)
            store.save(job)
            await publish(job)

    @app.post("/api/jobs/{job_id}/plan")
    async def make_plan(job_id: str, request: PlanRequest) -> dict:
        job = load_job(job_id)
        if not job.transcript:
            raise HTTPException(409, "transcribe first")
        if job.chat_pending:
            raise HTTPException(409, "AI 正在执行对话操作")
        if not claim(job_id, "plan"):
            raise HTTPException(409, "job is busy")

        options = PlanOptions(
            mode="rules" if request.mode == "manual" else request.mode,
            limit=request.limit,
            min_seconds=request.min_seconds or cfg.min_clip_seconds,
            max_seconds=request.max_seconds or cfg.max_clip_seconds,
            trim_silence=request.trim_silence,
            instruction=request.instruction,
        )
        job.status = "planning"
        job.progress = 10
        job.message = "选段中"
        store.save(job)
        await publish(job)
        spawn(_plan(app, job_id, request, options))
        return job.to_dict()

    async def _plan(app: FastAPI, job_id: str, request: PlanRequest, options: PlanOptions) -> None:
        store: JobStore = app.state.store
        job = store.load(job_id)
        assert job.transcript is not None
        try:
            started = time.time()
            if request.mode == "manual":
                ids = request.sentence_ids or []
                if not ids:
                    raise ValueError("没有选中任何句子")
                clips = await asyncio.to_thread(
                    clips_from_sentences, job.transcript, ids, options
                )
                warning = None
            else:
                llm = LLMConfig(
                    api_key=cfg.llm_api_key,
                    model=cfg.llm_model,
                    base_url=cfg.llm_base_url,
                    timeout=cfg.llm_timeout,
                )
                clips, warning = await asyncio.to_thread(plan, job.transcript, options, llm=llm)

            if clips and options.trim_silence:
                media = store.media_path(job_id, job.media_file)
                try:
                    silences = await asyncio.to_thread(detect_silences, media)
                    await asyncio.to_thread(apply_silence_trim, clips, silences, options)
                except Exception as exc:  # noqa: BLE001 - trimming must not fail planning
                    warning = (warning + "；" if warning else "") + f"去停顿失败：{exc}"

            job.clips = clips
            store.cleanup_clips(job)
            job.status = "planned" if clips else "transcribed"
            job.progress = 100
            removed = sum(c.removed_pauses for c in clips)
            detail = f"{len(clips)} 个候选片段" if clips else "没有找到合适的片段"
            if removed:
                detail += f"，去停顿 {removed} 处"
            if request.mode != "manual":
                detail += f" · {time.time() - started:.1f}s"
            job.message = warning or detail
            job.error = None
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.message = "选段失败"
        finally:
            app.state.busy.pop(job_id, None)
            store.save(job)
            await publish(job)

    @app.post("/api/jobs/{job_id}/render")
    async def render(job_id: str, request: RenderRequest | None = None) -> dict:
        job = load_job(job_id)
        if not job.clips:
            raise HTTPException(409, "nothing to render")
        if job.chat_pending:
            raise HTTPException(409, "AI 正在执行对话操作")
        targets = request.clip_ids if request else None
        if targets is None:
            targets = [c.id for c in job.clips if c.status in ("planned", "error")]
        selected = [c for c in job.clips if c.id in set(targets)]
        if not selected:
            raise HTTPException(409, "no matching clips to render")
        if not claim(job_id, "render"):
            raise HTTPException(409, "job is busy")

        if request:
            if request.aspect:
                job.export_aspect = request.aspect
            if request.fit:
                job.aspect_fit = request.fit
            if request.subtitles is not None:
                job.export_subtitles = request.subtitles
            if request.quality:
                job.render_quality = request.quality
        for clip in selected:
            clip.status = "planned"
            clip.error = None
            clip.file = None
        job.status = "rendering"
        job.progress = 0
        job.message = f"导出 0/{len(selected)}"
        store.save(job)
        await publish(job)
        spawn(
            _render(
                app,
                job_id,
                [c.id for c in selected],
                request.aspect if request else None,
                request.subtitles if request else None,
                request.subtitle_style if request else "clean",
                request.quality if request else None,
                request.fit if request else None,
            )
        )
        return job.to_dict()

    async def _render(
        app: FastAPI,
        job_id: str,
        clip_ids: list[str],
        aspect: str | None = None,
        subtitles: bool | None = None,
        style: str = "clean",
        quality: str | None = None,
        fit: str | None = None,
    ) -> None:
        store: JobStore = app.state.store
        job = store.load(job_id)
        try:
            await asyncio.to_thread(
                render_selected,
                store,
                job,
                clip_ids,
                aspect=aspect,
                subtitles=subtitles,
                subtitle_style=style,
                quality=quality,
                fit=fit,
            )
        finally:
            app.state.busy.pop(job_id, None)
            await publish(store.load(job_id))

    # -- voiceover ---------------------------------------------------------
    @app.post("/api/jobs/{job_id}/clips/{clip_id}/voiceover")
    async def clip_voiceover(
        job_id: str, clip_id: str, request: VoiceoverRequest
    ) -> dict:
        job = load_job(job_id)
        if job.chat_pending:
            raise HTTPException(409, "AI 正在执行对话操作")
        if not claim(job_id, "voiceover"):
            raise HTTPException(409, "job is busy")
        try:
            result = await asyncio.to_thread(
                add_voiceover_to_clip,
                store,
                job,
                clip_id,
                request.text,
                voice=request.voice,
            )
            job = store.load(job_id)
            await publish(job)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - surface the real reason
            raise HTTPException(500, f"voiceover failed: {exc}") from exc
        finally:
            app.state.busy.pop(job_id, None)
        return {**result, "job": job.to_dict()}

    # -- JianYing draft export ---------------------------------------------
    @app.get("/api/jobs/{job_id}/export/draft")
    async def export_draft_zip(
        job_id: str, aspect: Literal["source", "9:16", "1:1"] = "source"
    ) -> FileResponse:
        job = load_job(job_id)
        if not job.clips:
            raise HTTPException(409, "plan clips first")
        source = store.media_path(job_id, job.media_file)
        info = await asyncio.to_thread(probe, source)
        width, height = canvas_for(aspect, info)
        root = store.job_dir(job_id) / "drafts"
        try:
            draft_dir = await asyncio.to_thread(
                export_draft,
                source,
                job.transcript.sentences if job.transcript else [],
                sorted(job.clips, key=lambda c: c.start),
                root,
                name=f"clipsmith-{job_id}",
                width=width,
                height=height,
            )
            archive = await asyncio.to_thread(zip_draft, draft_dir)
        except RuntimeError as exc:
            raise HTTPException(501, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - surface the real reason to the client
            raise HTTPException(500, f"draft export failed: {exc}") from exc
        return FileResponse(
            archive, media_type="application/zip", filename="clipsmith-draft.zip"
        )

    # -- conversation ------------------------------------------------------
    @app.get("/api/jobs/{job_id}/chat")
    async def get_chat(job_id: str) -> dict:
        job = load_job(job_id)
        return {"messages": load_chat(store, job_id), "pending": job.chat_pending}

    @app.post("/api/jobs/{job_id}/chat")
    async def send_chat(job_id: str, request: ChatRequest) -> dict:
        job = load_job(job_id)
        if not cfg.llm_enabled:
            raise HTTPException(400, "未配置 LLM，无法使用对话剪辑")
        if job.chat_pending:
            raise HTTPException(409, "上一条指令还在执行")
        if job_id in app.state.busy:
            raise HTTPException(409, "任务正在执行其他操作")

        if request.aspect is not None:
            job.export_aspect = request.aspect
        if request.fit is not None:
            job.aspect_fit = request.fit
        if request.subtitles is not None:
            job.export_subtitles = request.subtitles
        if request.trim_silence is not None:
            job.trim_silence = request.trim_silence
        if request.quality is not None:
            job.render_quality = request.quality

        append_chat(store, job_id, "user", request.message.strip())
        job.chat_pending = True
        job.error = None
        store.save(job)
        await publish(job)
        spawn(_chat(app, job_id))
        return {"pending": True}

    async def _chat(app: FastAPI, job_id: str) -> None:
        store: JobStore = app.state.store
        agent = ClipAgent(store, job_id, cfg, app.state.engine, publish)
        try:
            await agent.run()
        except Exception as exc:  # noqa: BLE001 - always leave the chat usable
            job = store.load(job_id)
            append_chat(
                store, job_id, "assistant", f"执行失败：{type(exc).__name__}: {exc}"
            )
            job.chat_pending = False
            store.save(job)
            await publish(job)

    @app.patch("/api/jobs/{job_id}/options")
    async def update_options(job_id: str, request: OptionsRequest) -> dict:
        job = load_job(job_id)
        if job.chat_pending:
            raise HTTPException(409, "AI 正在执行对话操作")
        if request.aspect is not None:
            job.export_aspect = request.aspect
        if request.fit is not None:
            job.aspect_fit = request.fit
        if request.subtitles is not None:
            job.export_subtitles = request.subtitles
        if request.trim_silence is not None:
            job.trim_silence = request.trim_silence
        if request.quality is not None:
            job.render_quality = request.quality
        store.save(job)
        await publish(job)
        return job.to_dict()

    # -- SRT exports -------------------------------------------------------
    @app.get("/api/jobs/{job_id}/transcript.srt")
    async def transcript_srt(job_id: str) -> Response:
        job = load_job(job_id)
        if not job.transcript:
            raise HTTPException(409, "transcribe first")
        return Response(
            to_srt(job.transcript.sentences),
            media_type="application/x-subrip",
            headers={"Content-Disposition": 'attachment; filename="transcript.srt"'},
        )

    @app.get("/api/jobs/{job_id}/clips/{clip_id}/srt")
    async def clip_subtitle(job_id: str, clip_id: str) -> Response:
        job = load_job(job_id)
        if not job.transcript:
            raise HTTPException(409, "transcribe first")
        clip = next((c for c in job.clips if c.id == clip_id), None)
        if not clip:
            raise HTTPException(404, "clip not found")
        return Response(
            clip_srt(job.transcript.sentences, clip.ranges, clip.start, clip.end),
            media_type="application/x-subrip",
            headers={"Content-Disposition": f'attachment; filename="clip-{clip.id}.srt"'},
        )

    # -- SSE ---------------------------------------------------------------
    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str, request: Request) -> StreamingResponse:
        job = load_job(job_id)
        queue = bus.create_queue(job_id)

        async def stream():
            try:
                yield f"data: {json.dumps({'job': job.to_dict()}, ensure_ascii=False)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        payload = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finally:
                bus.remove_queue(job_id, queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- static frontend (production) --------------------------------------
    dist = cfg.frontend_dist
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")

    return app


app = create_app()