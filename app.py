from __future__ import annotations

import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

import plotly.express as px
import streamlit as st

st.set_page_config(page_title="RepoPulse", page_icon="📊", layout="wide")

# --- diagnostics: surface the real import error that Streamlit redacts -------
# Pre-check every third-party + local module so we can print the exact failure
# point to the page AND stderr, instead of a redacted "ImportError".
import importlib  # noqa: E402

_DIAG_MODULES = ["duckdb", "pandas", "httpx", "plotly", "streamlit", "repopulse"]
_diag_failures: list[str] = []
for _mod in _DIAG_MODULES:
    try:
        importlib.import_module(_mod)
    except Exception as _exc:  # noqa: BLE001
        _diag_failures.append(f"{_mod}: {type(_exc).__name__}: {_exc}")

if _diag_failures:
    msg = "RepoPulse 依赖检测失败: " + " | ".join(_diag_failures)
    print(msg, file=sys.stderr)
    st.error(msg)
    st.stop()

try:
    from repopulse.config import Settings
    from repopulse.github_client import GitHubAPIError
    from repopulse.metrics import Analytics, RiskFlag, Window
    from repopulse.pipeline import collect_repository
    from repopulse.sample_data import load_demo_data
    from repopulse.storage import Warehouse
except Exception:
    tb = traceback.format_exc()
    print(tb, file=sys.stderr)
    st.error("导入 repopulse 子模块失败,完整 traceback 见下方:")
    st.code(tb, language="python")
    st.write(f"- Python: {sys.version}")
    st.write(f"- sys.path: {sys.path}")
    st.stop()


def ensure_database(db_path: Path) -> None:
    with Warehouse(db_path) as warehouse:
        warehouse.initialize()


def available_repositories(db_path: Path) -> list[str]:
    ensure_database(db_path)
    with Analytics(db_path) as analytics:
        return analytics.repositories()


def metric_value(value: object, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.1f}{suffix}"
    if isinstance(value, int):
        return f"{value:,}{suffix}"
    return f"{value}{suffix}"


def show_risk(flag: RiskFlag) -> None:
    if flag.level == "high":
        st.error(f"**{flag.title}**  {flag.detail}")
    elif flag.level == "medium":
        st.warning(f"**{flag.title}**  {flag.detail}")
    else:
        st.success(f"**{flag.title}**  {flag.detail}")


def report_to_csv(report) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["指标", "数值"])
    for key, value in report.metrics.items():
        writer.writerow([key, value])
    return buf.getvalue()


def resolve_window(choice: str, custom: object) -> Window:
    now = datetime.now(UTC)
    if choice == "自定义":
        # st.date_input returns a single date or a tuple of dates once both are
        # picked; before that it may be empty. Only build a window when we have
        # two concrete dates.
        if isinstance(custom, (tuple, list)) and len(custom) == 2 and all(custom):
            start = datetime.combine(custom[0], datetime.min.time()).replace(tzinfo=UTC)
            end = datetime.combine(custom[1], datetime.max.time()).replace(tzinfo=UTC)
            return Window(start=start, end=end)
        return Window.all()
    days = {"全部": None, "最近 30 天": 30, "最近 90 天": 90, "最近 180 天": 180}[choice]
    if days is None:
        return Window.all()
    return Window(start=now - timedelta(days=days), end=None)


settings = Settings.from_env()
st.title("RepoPulse")
st.caption("GitHub 开源项目健康度、维护效率与贡献者增长分析")

# Prefer a committed real-data snapshot if the active database is empty. This is
# what lets the Streamlit Cloud demo show fresh data from the daily refresh job.
snapshot_path = Path("data/snapshot/repopulse.duckdb")

repositories = available_repositories(settings.db_path)
if not repositories and snapshot_path.exists():
    settings = settings.__class__(
        repository=settings.repository,
        db_path=snapshot_path,
        github_token=settings.github_token,
        max_pages=settings.max_pages,
        demo_mode=settings.demo_mode,
    )
    repositories = available_repositories(settings.db_path)

if settings.demo_mode and not repositories:
    load_demo_data(settings.db_path)
    repositories = available_repositories(settings.db_path)

# ---------------------------------------------------------------- sidebar --
with st.sidebar:
    st.header("时间范围")
    window_choice = st.radio(
        "分析窗口",
        ["全部", "最近 30 天", "最近 90 天", "最近 180 天", "自定义"],
        index=2,
        horizontal=False,
    )
    custom_range = None
    if window_choice == "自定义":
        custom_range = st.date_input("起止日期", value=(), key="custom_window")

    st.divider()

    if settings.demo_mode:
        st.header("在线演示")
        st.success("当前为安全只读模式")
        st.caption("使用固定种子生成的匿名示例数据，不消耗 GitHub API 额度。")
    else:
        st.header("数据源")
        repository_input = st.text_input("GitHub 仓库", value=settings.repository)
        token = st.text_input(
            "GitHub Token（可选）",
            value=settings.github_token or "",
            type="password",
            help="Token 仅用于本次采集，不会写入数据库。",
        )
        max_pages = st.slider("单类数据最大页数", 1, 30, settings.max_pages)
        if st.button("采集/更新真实数据", type="primary", use_container_width=True):
            try:
                with st.spinner("正在从 GitHub 增量采集数据…"):
                    result = collect_repository(
                        repository_input,
                        settings.db_path,
                        token=token or None,
                        max_pages=max_pages,
                    )
                st.success(f"更新完成，本次处理 {result.total_loaded:,} 条记录。")
                st.rerun()
            except (GitHubAPIError, ValueError) as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"采集失败：{exc}")

        if st.button("加载离线示例数据", use_container_width=True):
            load_demo_data(settings.db_path)
            st.success("示例数据已加载。")
            st.rerun()

if not repositories:
    st.info("数据库还是空的。可以在左侧采集真实仓库，或加载无需网络的示例数据。")
    st.stop()

window = resolve_window(window_choice, custom_range)

mode = st.tabs(["单仓库分析", "多仓库对比"])


# ------------------------------------------------------- single-repo tab --
with mode[0]:
    selected = st.selectbox("分析仓库", repositories, index=0)

    with Analytics(settings.db_path) as analytics:
        overview = analytics.overview(selected)
        issue = analytics.issue_kpis(selected, window)
        pr = analytics.pr_kpis(selected, window)
        contributor = analytics.contributor_kpis(selected, window)
        issue_resp = analytics.issue_response_kpis(selected, window)
        pr_resp = analytics.pr_response_kpis(selected, window)
        backlog = analytics.backlog_kpis(selected, window)

        st.subheader(selected)
        if overview.get("description"):
            st.write(overview["description"])

        cols = st.columns(3)
        cols[0].metric("Stars", metric_value(overview.get("stars")))
        cols[1].metric("Issues（窗口内）", metric_value(issue.get("total")))
        cols[2].metric("PR 合并率", metric_value(pr.get("merge_rate"), "%"))
        cols = st.columns(3)
        cols[0].metric("Issue 关闭率", metric_value(issue.get("close_rate"), "%"))
        cols[1].metric("90天活跃贡献者", metric_value(contributor.get("active_90d")))
        cols[2].metric("发布版本", metric_value(overview.get("releases")))

        overview_tab, contributor_tab, operations_tab = st.tabs(
            ["活跃度总览", "贡献者增长", "维护效率与风险"]
        )

        with overview_tab:
            monthly = analytics.monthly_activity(selected, window)
            if not monthly.empty:
                figure = px.line(
                    monthly,
                    x="month",
                    y="activity_count",
                    color="activity_type",
                    markers=True,
                    labels={
                        "month": "月份",
                        "activity_count": "活动数量",
                        "activity_type": "活动类型",
                    },
                )
                figure.update_layout(legend_title_text="")
                st.plotly_chart(figure, use_container_width=True)
            st.caption(
                "口径：Issue/PR 按创建时间计入，Commit 按提交时间计入；"
                "时间范围筛选作用于 created_at。"
            )

        with contributor_tab:
            left, right = st.columns([1, 1.35])
            top = analytics.top_contributors(selected, window)
            with left:
                st.markdown("#### 核心贡献者")
                if not top.empty:
                    figure = px.bar(
                        top.sort_values("total_activity"),
                        x=["commits", "pull_requests"],
                        y="author",
                        orientation="h",
                        labels={"value": "活动数量", "author": "贡献者", "variable": "类型"},
                    )
                    st.plotly_chart(figure, use_container_width=True)
            with right:
                st.markdown("#### 月度 Cohort 留存")
                retention = analytics.contributor_retention(selected, window)
                if retention.empty:
                    st.info("数据不足，暂时无法计算贡献者留存。")
                else:
                    matrix = retention.pivot(
                        index="cohort_month", columns="month_number", values="retention_rate"
                    )
                    matrix.index = matrix.index.strftime("%Y-%m")
                    figure = px.imshow(
                        matrix,
                        text_auto=".0f",
                        aspect="auto",
                        color_continuous_scale="Blues",
                        labels={"x": "加入后的月份", "y": "首次贡献月份", "color": "留存率%"},
                        zmin=0,
                        zmax=100,
                    )
                    st.plotly_chart(figure, use_container_width=True)
            st.caption(
                "贡献者活动口径：提交 Commit 或创建 PR；"
                "同一贡献者同月多次活动只计算一次留存。"
            )

        with operations_tab:
            st.markdown("#### 维护效率")
            left, middle, right = st.columns(3)
            left.metric(
                "Issue 中位关闭时间",
                metric_value(issue.get("median_close_hours"), " 小时"),
            )
            middle.metric(
                "Issue P90 关闭时间",
                metric_value(issue.get("p90_close_hours"), " 小时"),
            )
            right.metric(
                "PR 中位合并时间",
                metric_value(pr.get("median_merge_hours"), " 小时"),
            )

            left, middle, right = st.columns(3)
            left.metric(
                "Issue 首次响应（中位）",
                metric_value(issue_resp.get("median_first_response_hours"), " 小时"),
            )
            middle.metric(
                "Issue 首次响应（P90）",
                metric_value(issue_resp.get("p90_first_response_hours"), " 小时"),
            )
            right.metric("无响应 Issue", metric_value(issue_resp.get("no_response")))

            left, middle, right = st.columns(3)
            left.metric(
                "PR 首次 Review（中位）",
                metric_value(pr_resp.get("median_first_review_hours"), " 小时"),
            )
            middle.metric(
                "PR 首次 Review（P90）",
                metric_value(pr_resp.get("p90_first_review_hours"), " 小时"),
            )
            right.metric("无 Review PR", metric_value(pr_resp.get("no_review")))
            st.caption(
                "首次响应/Review 已排除作者自评和 Bot（dependabot、[bot]、codecov 等）；"
                "无响应单独计数，不参与中位数计算。"
            )

            st.markdown("#### 积压")
            left, middle, right = st.columns(3)
            left.metric("开放 Issue", metric_value(backlog.get("open_issues")))
            middle.metric("超 30 天", metric_value(backlog.get("issue_stale_30")))
            right.metric("超 90 天占比", metric_value(backlog.get("issue_backlog_90_pct"), "%"))

            st.markdown("#### 风险提示")
            for flag in analytics.risk_flags(selected, window):
                show_risk(flag)

            st.markdown("#### 最近采集记录")
            runs = analytics.recent_runs(selected)
            st.dataframe(runs, use_container_width=True, hide_index=True)

    # --- report export (outside the analytics block: opens its own reader) ---
    st.divider()
    with st.expander("📋 一键导出分析报告（主要发现 / 风险 / 建议）"):
        from repopulse.report import build_report

        report = build_report(
            Analytics(settings.db_path), selected, window
        )
        st.markdown(report.to_markdown())
        left, right = st.columns(2)
        left.download_button(
            "下载 HTML 报告",
            report.to_html().encode("utf-8"),
            file_name=f"repopulse_{selected.replace('/', '_')}.html",
            mime="text/html",
        )
        right.download_button(
            "下载指标明细 CSV",
            report_to_csv(report).encode("utf-8-sig"),
            file_name=f"repopulse_{selected.replace('/', '_')}.csv",
            mime="text/csv",
        )


# --------------------------------------------------------- compare tab ----
with mode[1]:
    st.subheader("多仓库对比")
    compare_default = repositories[: min(3, len(repositories))]
    selected_repos = st.multiselect(
        "选择 2~5 个仓库", repositories, default=compare_default
    )
    if len(selected_repos) < 2:
        st.info("请至少选择 2 个仓库进行对比。")
    else:
        with Analytics(settings.db_path) as analytics:
            comp = analytics.comparison_kpis(selected_repos, window)

        st.markdown("#### 指标对比表")
        st.dataframe(comp, use_container_width=True, hide_index=True)

        left, right = st.columns(2)
        with left:
            st.markdown("#### PR 合并率")
            st.plotly_chart(
                px.bar(comp, x="repository", y="pr_merge_rate", text_auto=".1f"),
                use_container_width=True,
            )
        with right:
            st.markdown("#### Issue 关闭率")
            st.plotly_chart(
                px.bar(comp, x="repository", y="issue_close_rate", text_auto=".1f"),
                use_container_width=True,
            )

        left, right = st.columns(2)
        with left:
            st.markdown("#### PR 中位合并时间（小时）")
            st.plotly_chart(
                px.bar(comp, x="repository", y="pr_median_merge_hours", text_auto=".1f"),
                use_container_width=True,
            )
        with right:
            st.markdown("#### Issue 中位关闭时间（小时）")
            st.plotly_chart(
                px.bar(comp, x="repository", y="issue_median_close_hours", text_auto=".1f"),
                use_container_width=True,
            )

        left, right = st.columns(2)
        with left:
            st.markdown("#### 90天活跃贡献者")
            st.plotly_chart(
                px.bar(comp, x="repository", y="active_90d", text_auto=True),
                use_container_width=True,
            )
        with right:
            st.markdown("#### 头部贡献者集中度")
            st.plotly_chart(
                px.bar(comp, x="repository", y="top_contributor_share", text_auto=".1f"),
                use_container_width=True,
            )

        st.download_button(
            "下载对比数据 CSV",
            comp.to_csv(index=False).encode("utf-8-sig"),
            file_name="repopulse_comparison.csv",
            mime="text/csv",
        )

st.caption("RepoPulse 0.2 · 指标用于项目运营诊断，不代表因果结论。")
