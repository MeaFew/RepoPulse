"""Narrative report generation for a single repository.

Turns the structured KPIs + risk flags into "main findings, risks and
recommendations" prose, so the analysis product delivers a conclusion and not
just numbers. Output is plain text/HTML so it can be downloaded from the UI or
emailed from a scheduled job without extra dependencies.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from repopulse.metrics import Analytics, RiskFlag, Window


@dataclass(frozen=True)
class Report:
    repository: str
    generated_at: datetime
    window: Window
    findings: list[str]
    risks: list[RiskFlag]
    recommendations: list[str]
    metrics: dict[str, Any]

    def to_markdown(self) -> str:
        lines = [
            f"# RepoPulse 分析报告 · {self.repository}",
            "",
            f"生成时间：{self.generated_at:%Y-%m-%d %H:%M UTC}",
            _window_label(self.window),
            "",
            "## 主要发现",
        ]
        lines.extend(f"- {f}" for f in self.findings or ["窗口内数据不足，无法归纳主要发现。"])

        lines.append("")
        lines.append("## 风险")
        if not self.risks:
            lines.append("- 未触发任何风险阈值。")
        else:
            for flag in self.risks:
                lines.append(f"- **[{_level_label(flag.level)}] {flag.title}**：{flag.detail}")

        lines.append("")
        lines.append("## 建议")
        lines.extend(f"- {r}" for r in self.recommendations or ["保持现有维护节奏。"])

        lines.append("")
        lines.append("## 指标明细")
        for key, value in self.metrics.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def to_html(self) -> str:
        # Findings/risks/metrics text can originate from GitHub API responses,
        # so every interpolated value is escaped to keep the report inert HTML.
        findings = (
            "".join(f"<li>{html.escape(f)}</li>" for f in self.findings) or "<li>数据不足</li>"
        )
        risks = (
            "".join(
                f'<li class="risk-{html.escape(flag.level)}">'
                f"<strong>{html.escape(flag.title)}</strong>：{html.escape(flag.detail)}</li>"
                for flag in self.risks
            )
            or "<li>未触发风险阈值</li>"
        )
        recs = "".join(
            f"<li>{html.escape(r)}</li>" for r in self.recommendations
        ) or "<li>保持现状</li>"
        metrics_rows = "".join(
            f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
            for k, v in self.metrics.items()
        )
        repository = html.escape(self.repository)
        return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>RepoPulse 报告 · {repository}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         margin: 2rem auto; max-width: 760px; color: #1f2328; line-height: 1.6; }}
  h1 {{ font-size: 1.5rem; }}
  .meta {{ color: #57606a; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  .risk-high {{ color: #cf222e; }}
  .risk-medium {{ color: #bf8700; }}
  .risk-good {{ color: #1a7f37; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
  td, th {{ border: 1px solid #d0d7de; padding: 0.4rem 0.6rem; text-align: left; }}
  th {{ background: #f6f8fa; }}
</style></head><body>
<h1>RepoPulse 分析报告 · {repository}</h1>
<div class="meta">生成时间：{self.generated_at:%Y-%m-%d %H:%M UTC}{_window_label(self.window)}</div>
<h2>主要发现</h2><ul>{findings}</ul>
<h2>风险</h2><ul>{risks}</ul>
<h2>建议</h2><ul>{recs}</ul>
<h2>指标明细</h2><table>{metrics_rows}</table>
</body></html>
"""


def build_report(
    analytics: Analytics,
    repository: str,
    window: Window | None = None,
) -> Report:
    """Assemble a Report from the analytics layer: pull all KPIs, derive findings
    and recommendations with simple threshold rules, and surface every risk flag.
    """
    window = window or Window.all()
    overview = analytics.overview(repository)
    issue = analytics.issue_kpis(repository, window)
    pr = analytics.pr_kpis(repository, window)
    contributor = analytics.contributor_kpis(repository, window)
    issue_resp = analytics.issue_response_kpis(repository, window)
    pr_resp = analytics.pr_response_kpis(repository, window)
    backlog = analytics.backlog_kpis(repository, window)
    coverage = analytics.data_coverage(repository)
    quality_flags = analytics.data_quality_flags(repository)
    tasks = analytics.maintainer_tasks(repository, window)
    risks = analytics.risk_flags(repository, window)

    findings: list[str] = []
    recommendations: list[str] = []

    if not coverage.empty:
        complete = int(coverage["history_complete"].sum())
        findings.append(f"{complete}/{len(coverage)} 类数据未发现历史分页缺口。")
    if not tasks.empty:
        high_priority = int((tasks["priority"] == "高").sum())
        findings.append(f"维护者待办共 {len(tasks)} 项，其中高优先级 {high_priority} 项。")
        recommendations.append("优先处理报告中的高优先级陈旧 Issue 和等待 Review 的 PR。")

    if (close_rate := issue.get("close_rate")) is not None:
        findings.append(f"Issue 关闭率为 {close_rate}%。")
    if (merge_rate := pr.get("merge_rate")) is not None:
        findings.append(f"非草稿 PR 合并率为 {merge_rate}%。")

    median_merge = pr.get("median_merge_hours")
    if median_merge is not None and median_merge > 24 * 3:
        findings.append(f"PR 中位合并耗时 {median_merge} 小时（超过 3 天）。")
        recommendations.append("检查 PR Review 流程，考虑引入自动分配 Reviewer 或拆分大 PR。")
    elif median_merge is not None:
        recommendations.append("PR 合并节奏健康，维持现有 Review 流程。")

    median_resp = issue_resp.get("median_first_response_hours")
    if median_resp is not None:
        findings.append(f"Issue 首次响应中位耗时 {median_resp} 小时。")
        if median_resp > 72:
            recommendations.append("首次响应偏慢，可设置 Issue 分流看板或自动标签提醒。")

    no_response = issue_resp.get("no_response") or 0
    total_issues = issue_resp.get("total_issues") or 0
    if total_issues and no_response / total_issues >= 0.2:
        ratio = no_response / total_issues
        findings.append(f"窗口内 {no_response} 个 Issue（{ratio:.0%}）完全没有响应。")
        recommendations.append("无响应比例偏高，建议定期巡检并主动关闭或回应陈旧 Issue。")

    backlog_pct = backlog.get("issue_backlog_90_pct")
    if backlog_pct is not None and backlog_pct >= 40:
        recommendations.append("开放 Issue 老化严重，建议做一次积压清理，明确里程碑或关闭无效项。")

    concentration = contributor.get("top_contributor_share") or 0
    if concentration >= 50:
        recommendations.append(
            "活动高度集中于单一贡献者，存在 Bus Factor 风险，建议培养第二维护者。"
        )

    if (active := contributor.get("active_90d")) is not None:
        findings.append(f"近 90 天活跃贡献者 {active} 人。")

    metrics = {
        "Stars": overview.get("stars"),
        "Issue 总数（窗口）": issue.get("total"),
        "Issue 关闭率 %": issue.get("close_rate"),
        "Issue 中位关闭时间（小时）": issue.get("median_close_hours"),
        "Issue 首次响应中位（小时）": median_resp,
        "无响应 Issue": no_response,
        "PR 合并率 %": merge_rate,
        "PR 中位合并时间（小时）": median_merge,
        "PR 首次 Review 中位（小时）": pr_resp.get("median_first_review_hours"),
        "开放 Issue": backlog.get("open_issues"),
        "超 90 天 Issue 占比 %": backlog_pct,
        "近 90 天活跃贡献者": active,
        "头部贡献者集中度 %": concentration,
        "数据覆盖完整实体": (
            f"{int(coverage['history_complete'].sum())}/{len(coverage)}"
            if not coverage.empty
            else "未知"
        ),
        "维护者待办": len(tasks),
    }

    return Report(
        repository=repository,
        generated_at=datetime.now(UTC),
        window=window,
        findings=findings,
        risks=[f for f in [*quality_flags, *risks] if f.level != "good"],
        recommendations=recommendations,
        metrics=metrics,
    )


def _level_label(level: str) -> str:
    return {"high": "高", "medium": "中", "good": "良好"}.get(level, level)


def _window_label(window: Window) -> str:
    if window.start is None and window.end is None:
        return "\n时间范围：全部"
    parts = []
    if window.start:
        parts.append(f"从 {window.start:%Y-%m-%d}")
    if window.end:
        parts.append(f"至 {window.end:%Y-%m-%d}")
    return "；时间范围：" + (" ".join(parts) if parts else "全部")
