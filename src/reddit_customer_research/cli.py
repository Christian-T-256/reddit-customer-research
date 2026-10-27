"""Command line entry point for offline demos and live Reddit research."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .analysis import analyze, render_report
from .llm import generate_summary
from .models import ResearchDataset
from .reddit import RedditClient, collect_research


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = PROJECT_ROOT / "fixtures" / "fictional_reddit_research.json"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reddit-customer-research",
        description="Turn public Reddit discussions into a source-linked buyer research report.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="Run entirely offline with fictional fixture data.")
    demo.add_argument("--output", type=Path, help="Optionally save the Markdown report.")
    demo.add_argument("--force", action="store_true", help="Allow overwriting --output.")

    live = subparsers.add_parser("live", help="Collect current public Reddit discussions.")
    live.add_argument("--niche", required=True, help="Product category or buyer niche.")
    live.add_argument("--subreddits", required=True, type=_csv, help="Comma-separated names.")
    live.add_argument("--keywords", default=[], type=_csv, help="Comma-separated search terms.")
    live.add_argument("--competitors", default=[], type=_csv, help="Comma-separated names to track.")
    live.add_argument("--posts-per-feed", type=_positive_int, default=25)
    live.add_argument("--max-comment-threads", type=_positive_int, default=8)
    live.add_argument("--minimum-comments", type=_positive_int, default=5)
    live.add_argument("--output", type=Path, help="Optionally save the Markdown report.")
    live.add_argument("--raw-output", type=Path, help="Optionally save normalized source data.")
    live.add_argument("--force", action="store_true", help="Allow overwriting output files.")
    return parser


def _write(path: Path, content: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite {path}. Pass --force to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_demo() -> ResearchDataset:
    return ResearchDataset.from_dict(json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8")))


def _run_demo(args: argparse.Namespace) -> str:
    dataset = _load_demo()
    report = render_report(analyze(dataset))
    if args.output:
        _write(args.output, report, force=args.force)
    return report


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _run_live(args: argparse.Namespace) -> str:
    client = RedditClient(
        user_agent=os.environ.get(
            "REDDIT_USER_AGENT", "reddit-customer-research/1.0 (portfolio demo)"
        ),
        request_delay=_float_env("REDDIT_REQUEST_DELAY_SECONDS", 1.1),
        timeout=_float_env("REDDIT_TIMEOUT_SECONDS", 15.0),
        max_attempts=_int_env("REDDIT_MAX_ATTEMPTS", 3),
        rate_limit_backoff=_float_env("REDDIT_RATE_LIMIT_BACKOFF_SECONDS", 30.0),
    )
    dataset = collect_research(
        client,
        niche=args.niche,
        subreddits=args.subreddits,
        keywords=args.keywords,
        competitors=args.competitors,
        posts_per_feed=min(args.posts_per_feed, 100),
        max_comment_threads=min(args.max_comment_threads, 20),
        minimum_comments=args.minimum_comments,
    )
    findings = analyze(dataset)
    report = render_report(findings, llm_summary=generate_summary(findings.summary_payload()))
    if args.output:
        _write(args.output, report, force=args.force)
    if args.raw_output:
        _write(
            args.raw_output,
            json.dumps(dataset.to_dict(), indent=2, ensure_ascii=True) + "\n",
            force=args.force,
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = _run_demo(args) if args.command == "demo" else _run_live(args)
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
