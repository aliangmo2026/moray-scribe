# Changelog

## [0.1.0] - 2026-10-28

首个公开版本。

### 新特性

- **四大模式**：`scan` / `graph` / `diagram` / `doc`——从符号扫描到完整架构文档。
- **五层分析**：libcst 静态扫描 → 图论分析（PageRank 枢纽 / 死代码 / 入口点）→
  Token 预算裁剪 → 本地大模型语义生成 → Mermaid + Markdown 渲染。
- **严格契约**：大模型只能输出经 Pydantic 强校验的 JSON，不写任何代码——零幻觉。
- **AI / 引擎分区**：`ARCHITECTURE.md` 显式区分"模型推断"与"引擎确定性统计"，可复现、可信任。
- **MCP 接入**：6 个 tool（`scribe_health` / `scribe_scan` / `scribe_graph` /
  `scribe_doc` / `scribe_read_artifact` / `scribe_list_symbols`）可挂到 Cursor 等宿主。
- **本地优先**：Ollama + 你的模型，代码不出本机；7B 模型即可跑通。

### 已知限制（v0.1.0）

0. **硬件依赖明显**——doc 阶段真实生成需要 7B 模型在合理速度下运行。纯 CPU 推理
   下，1500 行项目约需 1-3 分钟，6000 行项目约需 5-15 分钟。若机器负载高
   （如 IDE 已占满 CPU），耗时可能翻倍。**这不是缺陷，是任务本身的推理成本。**
   推荐配置：GPU 或 M 系列芯片，或空闲的高性能 CPU。详见
   [README 已知限制](README.md#已知限制v010)。
1. **仅支持 Python**——扫描基于 libcst，是 Python 专用。JS / TS / Go 需等未来版本的
   tree-sitter 后端。*踩到场景*：扫描非 Python 目录会找不到可解析文件。
2. **动态调用不解析**——`getattr` / `eval` / `importlib.import_module` 构造的调用边
   会被漏掉。*踩到场景*：插件式 / 反射重度的代码库，`ARCHITECTURE.md` 的"已知缺口"
   章节会提示这些位置，需人工确认。
3. **多继承 `super()` 歧义跳过**——`class A(B, C)` 里 `super().foo()` 若 B、C 都有
   `foo`，边会被跳过（宁缺毋滥，不猜）。*踩到场景*：多继承 + 同名方法的框架代码。
4. **大项目需调参**——符号数 > 5000 的项目，默认 `max_nodes=200` 会裁掉关键模块。
   *踩到场景*：单体巨石仓库。建议 `--max-nodes 400` 并调大
   `MORAY_SCRIBE_MAX_OUTPUT_TOKENS`。
5. **CI / 自动化里跑 doc 需预留时间预算**——doc 阶段生成不可中断且依赖本机推理
   速度，流水线里应显式设 `MORAY_SCRIBE_REQUEST_TIMEOUT_S`（或 `--timeout`）并预期
   2-15 分钟。*踩到场景*：CI 默认 5 分钟超时直接杀掉任务。（第 0 条讲硬件选择，
   本条讲自动化超时预算。）
6. **类图优先级低于架构图**——首图强制 `flowchart`（架构图），`classDiagram` 只能作
   `diagrams[1:]` 的可选细节图。*踩到场景*：想直接要一张 UML 类图时需手动调整。
7. **R5-2 粒度检测可能误伤**——若项目真由 > 60% 的独立子系统组成（罕见），会被误判为
   "1:1 映射"并要求合并。*踩到场景*：高度解耦的微内核 / 插件集合。当前无 override 参数。

### 路线图（v0.2.0，planned）

- two-phase 生成（模块级摘要 → 全局架构），降低大项目单次生成压力
- JS / TS 支持（tree-sitter 后端）
- HTTP / SSE MCP transport
- 自动质量评分

[0.1.0]: https://github.com/moray/moray-scribe/releases/tag/v0.1.0
