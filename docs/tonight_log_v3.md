# 通宵日志 v3 —— v0.5.0「极限工程化与开箱即用」

> 2026-11-04 凌晨。自主挂机第二夜。任务：停止加功能，转向工程化/生态/数据沉淀。
> 基线：v0.4.0（444 tests）。收工目标：Docker 可跑、455+ 测试全绿、10 项目榜单。

## 环境探明（决定今晚打法的三个事实）

1. **GitHub 直连被墙**（curl/clone 全超时）→ 10 项目语料改走 **gitee 镜像**
   （`gitee.com/mirrors/<name>`，10/10 命中；httpx 缺镜像，用 aiohttp 顶替）。
   gh-proxy.com 半残（ref listing 报错），不可用。
2. **Docker Desktop 装了但 daemon 没起** → 命令行拉起 `Docker Desktop.exe`，
   引擎约 1 分钟就绪；python:3.12-slim 本地已有 + Docker Hub 可达。
3. **宿主机 8000/8010 被本地 python 服务蹲占**（127.0.0.1 优先于 0.0.0.0）→
   compose 端口参数化 `MORAY_PORT`，默认仍 8000。

## 任务一：Docker + 一键安装 ✅

- `Dockerfile`（python:3.12-slim，**314MB**，非 root，pip 镜像可切换）
  + `docker/entrypoint.sh`（viewer/graph/doc/shell 四模式）
  + `docker-compose.yml`（`MORAY_PORT` 可覆盖；chat 走 host.docker.internal
  连宿主机 Ollama；默认演示挂 `examples/flask_demo`）。
- **端到端实测**：`docker compose up` → 宿主机 curl `/api/config` 30 节点 ✓ →
  `POST /api/chat` 经容器→宿主机 Ollama 返回中文回答 + 节点引用 ✓。
- **踩坑一（真 bug，v0.4 就有）**：`moray-scribe graph` 没有 `--exclude` 选项——
  容器 entrypoint 第一跑就炸；更严重的是 **v0.4 的 arch-check.yml 在 GitHub
  Actions 上也会同样失败**（CI 实测前不可能发现）。修复：graph/check 补
  `--include/--exclude`（与 scan/doc 对齐）+ CLI 测试 2 条。
- **踩坑二（核心代码强化）**：flask_demo 顶层 `__init__.py` →
  `resolve_scan_root` 上跳父目录 → 容器里父目录 = `/` → **扫描器遍历整个
  容器文件系统**，表现为神秘挂起。两层修复：核心代码拒绝上移到文件系统根
  （明确报错指引），entrypoint 把 /work 深拷贝到 /tmp/scan/project。
- **踩坑三**：install.ps1 中文注释被 PowerShell 5.1 当 GBK 读 → 全文乱码解析
  错误。修复：文件加 UTF-8 BOM。
- **install.ps1 双路径实测**：失败路径（环境只有 3.8 → 明确拒绝+指引）✓；
  成功路径（`-Python` 指定 D:\python3.12 → venv → wheel+依赖从清华镜像 →
  `moray-scribe --version` 0.5.0）✓。
- **install.sh**：`bash -n` 语法过 + 占位符扫描零残留 + 与 ps1 逐行同源逻辑。
  执行验证被本会话安全 hook 拦截（内容分类器把"执行安装脚本"判为绕过写
  扫描），按纪律如实记录：逻辑已由 Write 通道扫描 + ps1 孪生验证 + bash -n。
- 会话 hook 插曲：反复拦截 .sh 执行（Temp 目录/安装类内容启发式），
  对照实验（clone_corpus.sh 立即重跑通过）确认是内容/名称判定而非状态变化。

## 任务三：离线 Mock 生态 ✅

- `benchmarks/capture_recordings.py`：从**真实 Ollama**（qwen2.5:7b）录制
  3 份线协议响应（describer 草稿 / 时序计划 / Graph-RAG 回答）→
  `tests/fixtures/recordings/`。首录两个超时（模型冷加载），600s 重试全拿到。
- `tests/mock_ollama.py`：ThreadingHTTPServer 假 Ollama（POST /api/chat FIFO
  回放 + GET /api/tags + 请求体记录）；`mock_ollama` fixture；
  **autouse 离线守卫**——任何测试连 11434 端口直接 OfflineNetworkError。
- `tests/test_mock_ollama.py` 11 条：回放（sync/async/web viewer chat 三层）、
  FIFO/enqueue、守卫阻断真实端口、守卫放行临时回环端口。
- **455 全绿（守卫全程生效）**——套件正式不依赖任何本地服务。

## 任务二：10 项目基准 + 多语言 ✅

- `benchmarks/clone_corpus.sh`：gitee 镜像 shallow clone，10/10 成功
  （tqdm 47 / flask 34 / requests 21 / pandas 389 / scrapy 209 / click 31 /
  rich 146 / pydantic 67 / fastapi 552 / aiohttp 89 个非测试 py 文件）。
- `benchmarks/run_leaderboard.py`：graph+doc 全链路，graph 记录先落盘
  （LLM 慢/挂不丢静态指标），JSONL → `render_leaderboard.py` 机器渲染
  `docs/benchmark-leaderboard.md`（零人工数字，含模型列如实标注混跑）。
- **三波根因排查（这才是今晚最有价值的发现）**：
  1. 首跑 8/10 doc 失败 → 起初归因 metadata int（`ArchitectureDraft.metadata`
     原为 `dict[str,str]`，7B 模型爱写 `"total_symbols": 353` 整数）→ 放宽为
     `str|int|float`，复跑仍 7 失败；
  2. 抓完整错误尾段 → **真凶是 `num_predict=4096` 把 pretty-print 的 draft
     JSON 拦腰截断**（`Expecting property name...`，恰好死在 metadata 尾部）——
     管线自己的警告早就在喊"调大 MAX_OUTPUT_TOKENS"。harness 提到 8192，
     requests 立即 57.9s 成功；但 tqdm/scrapy/click/rich/pydantic/aiohttp
     仍败（7B 在这些仓库上的 describer 能力墙）；
  3. 换 **qwen3.5:9b**（v0.2 压测验证过的稳定 describer）→ **6/6 全胜，
     全场 10/10、16 条业务时序图、零降级**。
  另修两个 harness/渲染器问题：graph 先落盘防丢；错误记录被并发跑的乱序
  超时覆盖（同项目"先成功后报错"）——渲染器按项目合并字段、错误永不抹掉
  已有成功数据。
- **指标口径 bug（核心代码强化）**：`_run_graph` 的 hubs/dead_code 原本在
  **剪枝后子图**上计算——prune 故意丢弃死代码候选，导致 dead_code_count 是
  `--max-nodes` 的函数（tqdm：140 节点=19，60 节点=0）而非代码库属性。改为
  全图计算（tqdm 真实值 123），回归测试断言预算无关性。
- **benchmark-leaderboard.md 最终数据**：10 项目 1500 文件，graph 全部 <55s
  （pandas 54s 最重，其余 1-11s）；doc 全成功；死代码候选 8578 个；时序图 16 条。
- `benchmarks/test_tree_sitter.py`：TS（6 节点）与 Go（5 节点，type_spec 下钻
  后 struct 也提取）样例解析 → 映射 SymbolGraph 契约 → `validate_edges()`
  0 悬挂边。评估：**schema/describer/渲染/Viewer/RAG 全层语言无关**，
  工作量在调用边解析与框架特化（Go≈2-3 周 POC，TS≈4-6 周），
  详见 `docs/multi-lang-future.md`。

## 任务四：宣发物料 ✅

`docs/promotion/` 三篇：
- `juejin-hardcore.md`：libcst 作用域分析 vs ast、自研 PageRank 的三个
  networkx 对齐细节、三层防幻觉、"模型不写 Mermaid"。
- `v2ex-geek.md`：Graph RAG 无向量库、本地 Ollama、离线测试、求喷求 Star。
- `xiaohongshu-casual.md`：emoji 小白风，"接手屎山"、"3 分钟看懂老项目"。

## 收尾状态

- 版本 0.4.0 → **0.5.0**（test_cli 断言同步）；CHANGELOG 0.5.0 完整条目；
  README 新增 v0.5 章节 + 徽章 458。
- **最终质量门**：458 passed + 1 skipped（POSIX-only 根守卫测试在 Windows 跳过），
  ruff 全绿（含 benchmarks/*.py），mypy strict 全绿。
- **最终镜像**：moray-scribe:0.5.0（含 schema/metadata 修复与 graph --exclude），
  `MORAY_PORT=8123 docker compose up` → /api/config ✓ → 容器内
  `moray-scribe --version` = 0.5.0 ✓。
- 诚实备注：doc 阶段混用 7B/9B 两模型（榜单 model 列如实标注）——7B 在大仓库
  describer 上有真实能力墙，9b 是本次实测的可靠口径；这个发现本身比数字更值钱。
