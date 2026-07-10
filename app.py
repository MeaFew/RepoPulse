from __future__ import annotations

from pathlib import Path

import plotly.express as px
import streamlit as st

from repopulse.config import Settings
from repopulse.github_client import GitHubAPIError
from repopulse.metrics import Analytics, RiskFlag
from repopulse.pipeline import collect_repository
from repopulse.sample_data import load_demo_data
from repopulse.storage import Warehouse

st.set_page_config(page_title="RepoPulse", page_icon="📊", layout="centered")


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


settings = Settings.from_env()
st.title("RepoPulse")
st.caption("GitHub 开源项目健康度与贡献者增长分析")

repositories = available_repositories(settings.db_path)
if settings.demo_mode and not repositories:
    load_demo_data(settings.db_path)
    repositories = available_repositories(settings.db_path)

with st.sidebar:
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

selected = st.selectbox("分析仓库", repositories, index=0)

with Analytics(settings.db_path) as analytics:
    overview = analytics.overview(selected)
    issue = analytics.issue_kpis(selected)
    pr = analytics.pr_kpis(selected)
    contributor = analytics.contributor_kpis(selected)

    st.subheader(selected)
    if overview.get("description"):
        st.write(overview["description"])

    cols = st.columns(3)
    cols[0].metric("Stars", metric_value(overview.get("stars")))
    cols[1].metric("Issues", metric_value(issue.get("total")))
    cols[2].metric("PR 合并率", metric_value(pr.get("merge_rate"), "%"))
    cols = st.columns(3)
    cols[0].metric("Issue 关闭率", metric_value(issue.get("close_rate"), "%"))
    cols[1].metric("90天活跃贡献者", metric_value(contributor.get("active_90d")))
    cols[2].metric("发布版本", metric_value(overview.get("releases")))

    overview_tab, contributor_tab, operations_tab = st.tabs(
        ["活跃度总览", "贡献者增长", "维护效率与风险"]
    )

    with overview_tab:
        monthly = analytics.monthly_activity(selected)
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
            "API 最大页数可能影响大型仓库的历史完整性。"
        )

    with contributor_tab:
        left, right = st.columns([1, 1.35])
        top = analytics.top_contributors(selected)
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
            retention = analytics.contributor_retention(selected)
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
        st.caption("贡献者活动口径：提交 Commit 或创建 PR；同一贡献者同月多次活动只计算一次留存。")

    with operations_tab:
        left, middle, right = st.columns(3)
        left.metric("Issue 中位关闭时间", metric_value(issue.get("median_close_hours"), " 小时"))
        middle.metric("Issue P90 关闭时间", metric_value(issue.get("p90_close_hours"), " 小时"))
        right.metric("超过90天的开放 Issue", metric_value(issue.get("stale_open")))
        left.metric("PR 中位合并时间", metric_value(pr.get("median_merge_hours"), " 小时"))
        middle.metric("PR P90 合并时间", metric_value(pr.get("p90_merge_hours"), " 小时"))
        right.metric(
            "头部贡献者活动占比",
            metric_value(contributor.get("top_contributor_share"), "%"),
        )

        st.markdown("#### 风险提示")
        for flag in analytics.risk_flags(selected):
            show_risk(flag)

        st.markdown("#### 最近采集记录")
        runs = analytics.recent_runs(selected)
        st.dataframe(runs, use_container_width=True, hide_index=True)

st.caption("RepoPulse 0.1 · 指标用于项目运营诊断，不代表因果结论。")
