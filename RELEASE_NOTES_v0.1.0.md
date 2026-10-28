# moray-scribe v0.1.0

> 🏗️ 接手的遗留代码看不懂？让它自己画出架构图。

moray-scribe 是一个遗留代码的逆向文档与架构图生成引擎：用 libcst 提取全项目符号图，用本地 Ollama 做业务推理，输出带 Mermaid 架构图的 `ARCHITECTURE.md`。**AI 推理和引擎统计严格分区**——模型说的和引擎算的分开写，可复现、可信任。

## 这个版本有什么

- **四条命令**：`scan` / `graph` / `diagram` / `doc`
- **五层分析**：libcst 扫描 → 图论分析（PageRank / 死代码）→ Token 预算裁剪 → 大模型语义生成 → Mermaid 渲染
- **零代码生成**：大模型只能输出 Pydantic 强约束的 JSON，源码格式由引擎渲染
- **MCP 接入**：6 个 tool 挂到 Cursor
- **本地优先**：Ollama + 7B 模型即可，代码不出本机

## 已知缺口（v0.1.0 如实声明）

- ⚠️ **`examples/` 当前为降级占位**——自我剖析产物因本机硬件限制未完成，将在 v0.1.1 补齐。管线完整性有 [真实项目验证报告](docs/realworld-report.md) 背书：用 qwen2.5:7b 在 psf/requests（6394 行）上 74 秒产出合法架构草稿。
- 仅支持 Python（libcst 限制）
- 动态调用（`getattr` / `eval` / `importlib`）不解析
- 多继承 `super()` 歧义跳过
- doc 阶段 CPU 推理需 2-10 分钟（见 README 已知限制第 0 条）

## 快速开始

```bash
pip install moray-scribe      # PyPI 稍后发布，当前请从源码安装
ollama pull qwen2.5:7b
moray-scribe doc ./your-legacy-project/ --out ./arch-doc/
```

## 安装（当前版本，从源码）

```bash
git clone https://github.com/<owner>/moray-scribe.git
cd moray-scribe
pip install -e .
```

## 验证

- 241 个测试全绿
- `ruff check .` clean、`mypy` clean
- 真实项目报告：[docs/realworld-report.md](docs/realworld-report.md)
- 架构设计：[docs/architecture.md](docs/architecture.md)

## 已知限制

见 [CHANGELOG.md](CHANGELOG.md#已知限制v010)（8 条）。

## 路线图（planned，不承诺时间）

- v0.1.1：补齐 examples、接首个用户 issue
- v0.2.0：two-phase 生成、R5-2 粒度检测 `--tolerance`
- v0.5+：JS/TS 支持（tree-sitter 后端）
