# moray-scribe

> 🏗️ **接手的遗留代码看不懂？让它自己画出架构图。**

几万行的 Python 老项目，没有文档、原作者已离职，市面的 AI 助手只能"打开一个文件问一句"，
你问不到点上，它也讲不清全局。

moray-scribe 用静态分析先把整个项目的符号与调用拓扑提出来，再让**你本机的**大模型做业务
推理，一次性生成分层架构图 + 模块业务说明——**AI 推理和引擎统计严格分区**，可复现、可信任。

它不是"逐文件解释器"：见[真实项目验证报告](docs/realworld-report.md)——在 `psf/requests`
（6394 行）上跑通扫描、建图、枢纽识别与 Mermaid 渲染的完整链路。

---

## 三个必须知道的点

1. **上帝视角，不是文件解释器**：一次扫描建全项目符号图，一次生成全局架构——不是逐文件问答。
2. **AI 不写代码**：大模型只能输出一份经 Pydantic 强校验的 Mermaid 数据 JSON；源码格式由引擎渲染，零幻觉。
3. **本地优先**：Ollama + 你的模型，代码不出本机。7B 模型就能跑。

## 30 秒上手

```bash
pip install moray-scribe
ollama pull qwen2.5:7b
moray-scribe doc ./your-legacy-project/ --out ./arch-doc/
# → ./arch-doc/ARCHITECTURE.md
```

前置：Python ≥ 3.10、[Ollama](https://ollama.com/)、任意 7B+ coder 模型（推荐 `qwen2.5:7b` / `qwen2.5-coder:14b`）。

## 它长什么样

`ARCHITECTURE.md` 顶部是一张 Mermaid 分层架构图（模块级，不是类级），往下是每个模块的
业务说明，再往下是**引擎算出来的**核心枢纽 / 死代码 / 已知缺口——AI 说什么、引擎算什么，
一眼分清。`.mmd` 图可直接粘进 [mermaid.live](https://mermaid.live) 渲染。

**能力证明**：见 [真实项目验证报告](docs/realworld-report.md)——用 qwen2.5:7b 在
`psf/requests`（6394 行）上原生生成架构草稿，评测结果、问题清单、改进建议全部公开。

**v0.1.1 预告**：`examples/` 目录将在硬件恢复后补充 moray-scribe 自我剖析产物
（当前机器纯 CPU 推理仅 ~2.5 tok/s，无法在超时内完成 4300 行项目的完整草稿）。

> `doc` 模式的完整生成依赖本地模型推理速度：7B 模型在千行级项目上通常需 2–10 分钟，
> 更大的项目或较慢的 CPU 建议调大 `MORAY_SCRIBE_REQUEST_TIMEOUT_S`（或 `--timeout`）与
> `MORAY_SCRIBE_MAX_OUTPUT_TOKENS`，或换用更快的模型 / GPU 加速。

## 四种模式

| 模式 | 输入 | 输出 | 需要 LLM |
|---|---|---|---|
| `scan` | 目录 | symbol-graph.json | ❌ |
| `graph` | 目录 | pruned-graph.json（含枢纽/死代码） | ❌ |
| `diagram` | 目录 | architecture-draft.json | ✅ |
| `doc` | 目录 | **ARCHITECTURE.md + .mmd** | ✅ |

```bash
moray-scribe scan ./legacy/ --out symbols.json
moray-scribe graph ./legacy/ --max-nodes 200
moray-scribe doc ./legacy/ --out ./arch-doc/
```

## 接入 Cursor（MCP）

三步挂进 Cursor / Claude Desktop：

1. **装**：`pip install -e .`（或 `pip install moray-scribe`），确认 `moray-scribe serve-mcp --help` 正常。
2. **配**：在 `.cursor/mcp.json`（或 Claude Desktop 配置）加入：
   ```json
   { "mcpServers": { "moray-scribe": {
       "command": "moray-scribe", "args": ["serve-mcp"],
       "env": { "MORAY_SCRIBE_MODEL_DESCRIBER": "qwen2.5:7b" } } } }
   ```
3. **用**：对话里让模型调用工具，它会自行选择：

| 工具 | 用途 |
|---|---|
| `scribe_health` | 检查 Ollama / 模型 / 输出目录是否就绪 |
| `scribe_scan` | 扫描符号清单（不调 LLM），返回摘要 |
| `scribe_graph` | 建图 + PageRank + 死代码，返回摘要 |
| `scribe_doc` | 生成 ARCHITECTURE.md + .mmd，返回路径与摘要 |
| `scribe_read_artifact` | 按章节/大小分块读回产物（保护上下文） |
| `scribe_list_symbols` | 分页查询符号图 |

## 设计哲学

- **物理分析精确 + 语义推理补缺**：拓扑边宁缺毋滥，缺口显式标注，不让模型瞎编。
- **AI / 引擎分区**：模型推断的和引擎计算的在文档里分区呈现，读者知道该信哪部分。
- **可复现**：同一输入两次运行，`ARCHITECTURE.md` 与 `.mmd` 字节一致（无时间戳、无随机）。

## 已知限制（v0.1.0）

0. **首次跑 doc 需要硬件准备**——7B 模型在纯 CPU 上生成 architecture draft 需要
   2-10 分钟（取决于项目规模与 CPU）。若你的机器被其他任务占用（IDE / 浏览器 / 其他
   模型服务），耗时会显著拉长。**建议**：doc 阶段前关闭其他 Ollama 请求，或设
   `MORAY_SCRIBE_REQUEST_TIMEOUT_S=900`（或 `--timeout 900`）。
   **排查提示**：若机器有独立 GPU 但 doc 阶段明显慢，先运行 `ollama ps`
   确认 Ollama 是否在用 GPU（`PROCESSOR` 列应为 `100% GPU` 或混合）。若显示
   `100% CPU`，参考 [Ollama GPU 配置](https://github.com/ollama/ollama/blob/main/docs/gpu.md)
   调整。
- 仅支持 Python（基于 libcst）
- 动态调用（`getattr` / `importlib`）不解析，会在"已知缺口"里提示
- 多继承 `super()` 歧义跳过（不猜）
- 大项目（> 5000 符号）需调小 `max_nodes`、调大 `MAX_OUTPUT_TOKENS`
- 首图强制 flowchart（架构图）；`classDiagram` 仅作可选细节图

详见 [CHANGELOG](CHANGELOG.md) 的完整限制列表。

## 文档

- [真实项目验证报告](docs/realworld-report.md)
- [架构设计说明](docs/architecture.md)
- [CHANGELOG](CHANGELOG.md)

## License

[MIT](LICENSE)
