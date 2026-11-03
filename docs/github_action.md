# GitHub Action：架构体检（arch-check）

> v0.4 新增。让 moray-scribe 成为开源项目的 **CI 架构门禁**：每次 PR 自动对比
> 图谱，**新增死代码**亮红灯，**核心枢纽剧变**自动评论报警。全流程冷启动
> < 2 分钟（引擎本身 0.15s，剩余都是装环境）。

## 一分钟接入

把 [`.github/workflows/arch-check.yml`](../.github/workflows/arch-check.yml)
拷进你的仓库即可，**无需任何 Secret、无需基线文件**——工作流会在 PR 的
merge-base 上现算基线图谱：

```yaml
# .github/workflows/arch-check.yml 的核心三步
- run: moray-scribe graph . --out .moray/before   # 在 PR base commit 上
- run: moray-scribe graph . --out .moray/after    # 在 PR head 上
- run: moray-scribe check . --baseline .moray/before/pruned-graph.json \
         --current .moray/after/pruned-graph.json \
         --report .moray/report.md --fail-on dead
```

效果：

| 场景 | 行为 |
|---|---|
| PR 引入了没人调用的公开函数 | `Architecture check` 步骤**红灯**，PR 被自动评论 |
| PR 让某个 top-10 PageRank 枢纽掉出榜/分数腰斩 | 报告里出现 ⚠️ 枢纽变化表（默认不挡合并） |
| 结构无变化 | 绿灯 + ✅ 报告 |

PR 评论示例：

```markdown
## 🏗️ 架构体检报告 · my-legacy-app

基线 412 节点 → 当前 415 节点。共 **2** 项需关注。

### 🚨 新增死代码（1）
- `app.legacy.old_login`

### ⚠️ 核心枢纽变化
| 符号 | 基线排名 → 当前排名 | 分数变化 |
|---|---|---|
| `core.Engine` | 1 → 出局 | 0.0412 → 0.0007 |
```

## `moray-scribe check` 命令

```bash
moray-scribe check PATH --baseline BASE.json [选项]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `PATH` | 必填 | 被检项目目录（未提供 `--current` 时现场跑 `graph`） |
| `--baseline` | 必填 | 基线 `pruned-graph.json`（PR 基点 / 上个 tag） |
| `--current` | 现场构建 | 已构建好的当前图谱，CI 里可省一次重复扫描 |
| `--hub-top` | `10` | PageRank 枢纽观察榜长度 |
| `--hub-score-drop` | `0.5` | 相对分数跌幅阈值（0.5 = 掉一半算“剧变”） |
| `--fail-on` | `dead` | 哪些发现导致非零退出：`dead` / `hub` / `any` / `none` |
| `--report` | 无 | 输出 PR 评论用的 Markdown 报告 |
| `--out` / `-o` | `out/` | 现场构建的当前图谱落盘位置 |
| `--max-nodes` | `200` | 传给 `graph` 阶段的剪枝预算 |

退出码：`0` = 通过；`1` = 命中 `--fail-on`；`2` = 基线/图谱文件损坏等用法错误。

### 判定语义

- **新增死代码**：`detect_dead_code` 在两个图谱上**现场重算**（零入度、非入口、
  非私有、非包门面），取 `当前 − 基线`。所以旧版本导出的基线也能比。
  PR 里新写的、还没接线的函数会命中；名字带 `_` 前缀、路由处理函数、`main`
  不会命中。
- **枢纽变化**：取两边 top-N PageRank 的并集，以下三种情况各记一条：
  1. 基线在榜、当前**出局**；
  2. 两边都在榜但分数**相对跌幅 ≥ hub-score-drop**；
  3. **首次进榜**的新枢纽。
  默认只评论不挡合并——枢纽迁移通常是重构的正常代价。

## 策略建议

### 1. 基线怎么选？

| 策略 | 做法 | 适用 |
|---|---|---|
| **现算基线**（默认工作流） | 在 `github.event.pull_request.base.sha` 上现跑 `graph` | 零维护，推荐 |
| **提交基线文件** | 把 `pruned-graph.json` 提交到 `.moray/baseline.json`，`--baseline` 指它 | 想让“体检”跨 PR 累积、tag 发布时更新 |
| **Release 基线** | `--baseline` 指向最近一次 release tag 的图谱 | 只关心相对上个版本的变化 |

现算基线每次 PR 多花一次 `graph`（大仓库 ~几十秒）；提交基线则要在重构时
手动刷新，否则会有持续噪音。**小仓库用现算，大仓库用提交基线 + 每周 cron 刷新**。

### 2. `--fail-on` 怎么选？

- 新项目 / 严格治理：`--fail-on any`（死代码、枢纽剧变都挡）。
- 遗留项目 / 引入期：默认 `--fail-on dead`（只挡新增死代码，枢纽变化只评论）。
- 只想看报告：`--fail-on none`（评论照发，永不挡）。

### 3. 降低噪音

- 排除测试目录：工作流里已带 `--exclude "**/tests/**"`；
- `paths: ["**.py"]` 触发过滤已保证纯文档 PR 不跑；
- 阈值调节：`--hub-top 5 --hub-score-drop 0.7` 只报最猛烈的剧变。

## 本地演练

```bash
# 1. 当前仓库建基线
moray-scribe graph examples/flask_demo --out /tmp/before
# 2. 随便加一个没人调用的函数
echo "def orphan(): pass" >> examples/flask_demo/app.py
# 3. 体检（退出码 1，打印报警）
moray-scribe check examples/flask_demo \
    --baseline /tmp/before/pruned-graph.json \
    --report /tmp/report.md --fail-on dead ; echo "exit=$?"
```

## 安全与权限

- 工作流请求 `contents: read` + `pull-requests: write`（评论所需的最小集），
  不碰 `pull_request_target`，不签任何 Secret——PR 代码不会拿到写权限；
- `gh pr comment` 使用仓库自带的 `GITHUB_TOKEN`；
- 引擎对被扫描仓库是**只读**的（产出只写到 `.moray/`）；
- `moray-scribe` 从 PyPI 安装、版本钉死 `>=0.4`，被检仓库的 `requirements.txt`
  不会污染工具本身。

## 局限（诚实声明）

- 静态保守原则：动态调用（`getattr`、字符串路由）不计入，除非该仓库自己跑
  `--infer`（CI 默认关，保证确定性）；
- 死代码是**候选**而非判决：零入度也可能是框架反射调用，报告建议人工确认后
  再删；
- PageRank 对图谱规模敏感：两次 `graph` 必须用相同的 `--max-nodes` 与
  `--exclude`（工作流里已固定），否则 diff 全是噪音；
- `check` 不校验“新增死代码”是否恰好是你刚删到一半的脚手架——报告只是把
  问题指出来。

## 实现索引

| 组件 | 位置 |
|---|---|
| diff 算法 | `src/moray_scribe/grapher/diff.py`（`diff_graphs` / `render_diff_markdown`） |
| CLI 子命令 | `src/moray_scribe/cli.py` 的 `check` |
| 工作流 | [`.github/workflows/arch-check.yml`](../.github/workflows/arch-check.yml) |
| 测试 | `tests/test_graph_diff.py`、`tests/test_cli_check.py`、`tests/test_workflow_config.py` |
