# moray-scribe Serverless Skill（Work Buddy 平台）

把 moray-scribe 的**纯静态分析能力**（扫描 + 符号图 + PageRank/死代码检测）打包成
Work Buddy 平台可上传的技能包。云端沙箱**没有 Ollama、没有本地文件系统**，因此这个包
刻意剥离了 `describer`（LLM 描述生成）和 `renderer`（Mermaid 渲染）两层——它们是唯一
依赖大模型的代码。

## 1. 构建与验证

```bash
# 构建 moray-scribe-skill.zip（自动校验 < 3MB、不夹带 describer/renderer）
python serverless_skill/build_zip.py

# 本地模拟云端 event 调用（针对暂存目录）
python serverless_skill/test_handler.py

# 端到端：把 zip 解压到临时目录后原样再跑一遍测试
python serverless_skill/test_handler.py --zip
```

产物：`serverless_skill/moray-scribe-skill.zip`（约 40 KB，远低于 3MB 上限）。

## 2. 上传步骤

1. 在 Work Buddy 平台创建/打开你的 Agent，进入 **技能（Skill）** 或 **工具（Tool）** 配置页。
2. 上传 `moray-scribe-skill.zip`。zip 根目录的 `SKILL.md` 是平台解析的技能元数据与
   输入 Schema 定义（与 `index.py` 同级），`requirements.txt` 供平台在云端
   `pip install`（libcst、networkx、numpy、scipy、pydantic、pydantic-settings——
   包本身不含任何三方库）。
3. 入口约定为模块 `index` 中的 `handler(event, context)` 函数。
4. 保存并部署，用下面的最小 event 做一次冒烟测试：

```json
{"files": {"app/main.py": "def main():\n    pass\n"}}
```

期望返回：`{"status": "success", "summary": {"total_files_parsed": 1, ...}}`。

## 3. 输入 / 输出协议

**输入**——`event` 是一个字典，核心键是 `files`：

| 键 | 类型 | 说明 |
|---|---|---|
| `files` | `dict[str, str]` | **必填**。键 = 相对路径（正斜杠，如 `pkg/a.py`），值 = 完整文件内容字符串 |
| `top_hubs` | `int` | 可选。返回枢纽数量，默认 10，上限 50 |

- 只有 `.py` 文件会被解析；解析失败的文件进入 `skipped`，不会导致整体失败。
- 顶层 `__init__.py` 没有包上下文，会被跳过（与真实扫描器行为一致）。
- `event` 也接受 JSON 字符串形式（部分平台以文本投递）。

**输出**——始终是 JSON 可序列化的字典：

```json
{
  "status": "success",
  "summary": {
    "total_files_parsed": 3,
    "total_files_skipped": 1,
    "total_symbols": 7,
    "total_edges": 5,
    "core_hubs": [{"id": "app.main.main", "score": 0.31}],
    "dead_code_candidates": ["app.utils.legacy.never_called"],
    "entry_points": ["app.main.main"]
  },
  "skipped": [{"path": "broken.py", "error": "SyntaxError: ..."}]
}
```

- `core_hubs`：PageRank 前 N 的枢纽符号（按分数降序），id 格式为 `模块名.限定名`。
- `dead_code_candidates`：无外部引用的公共符号**候选**（保守启发式，需人工复核）。
- `entry_points`：入口点（`main`/`__main__`/`__init__` 命名或带路由/CLI 装饰器）。
- 失败时返回 `{"error": "..."}`（无文件、全解析失败等）。

## 4. 在 Agent 提示词里教大模型传 `files` 字典

把下面这段放进 Agent 的 system prompt（或技能说明）：

```text
你可以使用 moray-scribe 技能对用户提供的 Python 代码做静态架构分析。
调用方式：构造 event = {"files": {相对路径: 文件内容字符串, ...}}，然后执行 handler(event)。

规则：
1. files 的键必须是相对路径，用正斜杠分隔（如 "src/pkg/module.py"），不要用绝对路径。
2. files 的值必须是该文件的完整源码字符串，不是文件名、不是摘要。
3. 只传 .py 文件；语法错误的文件会被自动跳过并记录在返回值的 skipped 里，无需预筛。
4. 可选 top_hubs（默认 10）控制返回多少个枢纽符号。
5. 解析返回的 summary：向用户报告符号总数、core_hubs（核心枢纽）、
   dead_code_candidates（死代码候选，需人工确认）、entry_points（入口点）。
6. 若返回 error，先检查 files 是否为空或内容是否为空字符串，再重试。
```

## 5. 边界说明

- 本包**不含** LLM 调用：没有 Ollama、没有 HTTP 客户端、没有 describer/renderer。
  所有结果都来自 libcst 语法树 + networkx PageRank 的确定性计算。
- `moray_scribe/utils/tokens.py` 会随包复制，但它依赖的 `tiktoken` 不在
  `requirements.txt` 里——静态分析路径永远不会 import 它。
- 打包脚本 `build_zip.py` 会在构建时断言 `describer`/`renderer`/`mcp`/`cli.py`/
  `pipeline.py` 等被禁止的路径没有混入 zip。
