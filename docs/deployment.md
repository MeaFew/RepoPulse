# 在线 Demo 与部署

RepoPulse 可以直接部署到 Streamlit Community Cloud。云端建议使用安全只读模式：访客查看预采集数据，但不能消耗维护者的 GitHub API 额度。

## 部署到 Streamlit Community Cloud

1. 将仓库推送到 GitHub。
2. 在 [Streamlit Community Cloud](https://share.streamlit.io/) 创建应用。
3. 选择仓库的 `main` 分支，将入口文件设为 `app.py`。
4. 在 Advanced settings 中选择 Python 3.12。
5. 将 `.streamlit/secrets.toml.example` 的内容复制到 Secrets。

推荐配置：

```toml
REPOPULSE_DEMO_MODE = "true"
REPOPULSE_DB_PATH = "/tmp/repopulse.duckdb"
REPOPULSE_SNAPSHOT_PATH = "data/snapshot/repopulse.duckdb"
REPOPULSE_MAX_PAGES = "3"
```

根目录的 `requirements.txt` 用于云端安装运行依赖；Docker 镜像与 CI 改用 uv 生成的 `requirements.lock` / `requirements-dev.lock` 锁定版本。

## 数据快照

`.github/workflows/refresh.yml` 中的 `Refresh analytics snapshot` 工作流每天运行一次，也支持手动触发。默认面板包含：

- `duckdb/duckdb`
- `pola-rs/polars`
- `tobymao/sqlglot`

工作流将多个仓库采集到同一份 `data/snapshot/repopulse.duckdb`，然后把快照提交回当前分支。在线应用每次部署后会优先把这份真实快照镜像到 `/tmp/repopulse.duckdb`：前者随 Git 部署、保持可复现，后者可供 DuckDB 建立连接和执行结构迁移。即使云端休眠后保留了旧的 `/tmp` 模拟库，新部署的真实快照也会覆盖它。只有快照缺失、为空或损坏时，页面才会明确提示并回退到确定性示例数据。

GitHub Actions 自带的 `GITHUB_TOKEN` 足以读取公开仓库。如果需要更高的 API 限额，可创建仓库 Secret `REPOPULSE_GITHUB_TOKEN`；Token 只用于采集请求，不会写入快照。

手动运行工作流时，可以通过 `repositories` 输入框传入逗号分隔的 `owner/name` 列表，覆盖默认面板。

## 本地验证云端模式

```powershell
# Windows PowerShell
$env:REPOPULSE_DEMO_MODE="true"
$env:REPOPULSE_DB_PATH="$env:TEMP/repopulse-cloud-demo.duckdb"
$env:REPOPULSE_SNAPSHOT_PATH="data/snapshot/repopulse.duckdb"
python -m streamlit run app.py
```

```bash
# macOS / Linux
export REPOPULSE_DEMO_MODE="true"
export REPOPULSE_DB_PATH="/tmp/repopulse-cloud-demo.duckdb"
export REPOPULSE_SNAPSHOT_PATH="data/snapshot/repopulse.duckdb"
python -m streamlit run app.py
```

页面左侧应显示“当前展示每日更新的真实仓库快照”，仓库选择器默认选中 `duckdb/duckdb`，并隐藏真实采集入口。如果快照不可用，则应明确显示“真实快照不可用，当前使用离线模拟数据”，而不是悄悄展示模拟数据。

## 冷启动与排障

Streamlit Community Cloud 会让长时间无人访问的应用休眠。首次访问出现唤醒页面属于正常现象，点击唤醒后通常需要等待片刻。

如果唤醒后仍无法进入应用，依次检查：

1. 应用日志中的依赖安装或导入错误。
2. 入口文件是否为 `app.py`，Python 版本是否为 3.12。
3. Secrets 的 TOML 格式是否正确。
4. `Refresh analytics snapshot` 最近一次运行是否成功。
5. 快照文件是否存在且未被 Git LFS 指针或空文件替代。
