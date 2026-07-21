from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from repopulse.config import Settings
from repopulse.metrics import Analytics
from repopulse.pipeline import collect_repository
from repopulse.sample_data import load_demo_data


def build_parser() -> argparse.ArgumentParser:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(
        prog="repopulse", description="GitHub repository health analytics"
    )
    parser.add_argument("--db", type=Path, default=settings.db_path, help="DuckDB file path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="Collect or refresh a GitHub repository")
    collect.add_argument("repository", nargs="?", default=settings.repository)
    collect.add_argument(
        "--token",
        default=None,
        help="GitHub Token（不推荐：参数值会留在 shell 历史中，请优先使用 GITHUB_TOKEN 环境变量）",
    )
    collect.add_argument("--max-pages", type=int, default=settings.max_pages)

    subparsers.add_parser("demo", help="Load deterministic offline demo data")

    summary = subparsers.add_parser("summary", help="Print key metrics as JSON")
    summary.add_argument("repository")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect":
        if args.token is not None:
            print(
                "警告：--token 会把密钥留在 shell 历史中，建议改用 GITHUB_TOKEN 环境变量。",
                file=sys.stderr,
            )
        token = args.token if args.token is not None else Settings.from_env().github_token
        result = collect_repository(
            args.repository,
            args.db,
            token=token,
            max_pages=max(1, args.max_pages),
        )
        print(
            json.dumps(
                {
                    "repository": result.repository,
                    "loaded": result.counts,
                    "total_loaded": result.total_loaded,
                    "truncated_entities": result.truncated_entities,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "demo":
        repository = load_demo_data(args.db)
        print(f"已加载示例数据：{repository} -> {args.db}")
        return 0

    if args.command == "summary":
        with Analytics(args.db) as analytics:
            payload = {
                "overview": analytics.overview(args.repository),
                "issues": analytics.issue_kpis(args.repository),
                "pull_requests": analytics.pr_kpis(args.repository),
                "contributors": analytics.contributor_kpis(args.repository),
                "coverage": analytics.data_coverage(args.repository).to_dict(orient="records"),
                "data_quality": [
                    asdict(flag) for flag in analytics.data_quality_flags(args.repository)
                ],
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
