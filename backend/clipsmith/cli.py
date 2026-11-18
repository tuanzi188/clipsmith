"""Headless entry points.

    clipsmith serve --port 8000
    clipsmith run input.mp4 -o out --engine mock --mode rules

`run` is the whole pipeline without the HTTP layer: useful for batch jobs and
for checking an engine on a new machine.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .config import Settings
from .engine import build_engine
from .jobs import JobStore
from .media import probe, render_clip
from .planner import LLMConfig, PlanOptions, plan


def _run(args: argparse.Namespace) -> int:
    source = Path(args.input).resolve()
    if not source.exists():
        print(f"input not found: {source}")
        return 2
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    settings.engine = args.engine
    store = JobStore(settings.data_dir)
    engine = build_engine(settings)

    job = store.create(source.name)
    dest = store.job_dir(job.id) / f"source{source.suffix.lower()}"
    shutil.copy2(source, dest)
    job.media_file = dest.name
    info = probe(dest)
    job.duration = info.duration
    store.save(job)
    print(f"job {job.id}: {info.duration:.1f}s")

    transcript = engine.transcribe(dest, store.job_dir(job.id))
    print(f"transcribed {len(transcript.sentences)} sentences via {engine.name}")
    if args.verbose:
        for sentence in transcript.sentences[:5]:
            print(f"  [{sentence.start:6.2f}] {sentence.text}")

    options = PlanOptions(
        mode=args.mode,
        limit=args.limit,
        min_seconds=args.min_seconds,
        max_seconds=args.max_seconds,
    )
    llm = LLMConfig(
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
    )
    clips, warning = plan(transcript, options, llm=llm)
    if warning:
        print(f"warning: {warning}")
    print(f"planned {len(clips)} clips")

    rendered = []
    for clip in clips:
        target = output / f"{clip.id}.mp4"
        render_clip(dest, target, clip.start, clip.end)
        clip.file = str(target)
        rendered.append(
            {
                "id": clip.id,
                "file": str(target),
                "start": clip.start,
                "end": clip.end,
                "score": clip.score,
                "hook": clip.hook,
                "reason": clip.reason,
            }
        )
        print(f"  -> {target.name}  {clip.start:6.1f}-{clip.end:6.1f}  {clip.hook}")

    print(json.dumps(rendered, ensure_ascii=False, indent=2))
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("clipsmith.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipsmith")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="transcribe + plan + render one file")
    run_parser.add_argument("input")
    run_parser.add_argument("-o", "--output", default="out")
    run_parser.add_argument(
        "--engine", default="mock", choices=["mock", "whisper", "funasr", "funclip"]
    )
    run_parser.add_argument("--mode", default="rules", choices=["rules", "llm"])
    run_parser.add_argument("--limit", type=int, default=5)
    run_parser.add_argument("--min-seconds", type=float, default=10)
    run_parser.add_argument("--max-seconds", type=float, default=60)
    run_parser.add_argument("-v", "--verbose", action="store_true")
    run_parser.set_defaults(func=_run)

    serve_parser = sub.add_parser("serve", help="run the HTTP API and UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.set_defaults(func=_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())