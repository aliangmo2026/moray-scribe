# 多语言支持的未来：JS/TS 与 Go 改造评估

> v0.5 的可行性实验数据来自 `benchmarks/test_tree_sitter.py`（真跑出结果，见下）。
> 结论先行：**TypeScript 与 Go 都能映射到现有 SymbolGraph 契约**，真正的工作量
> 不在"能不能解析"，而在三件事：调用边解析、入口点识别、领域特化重写。

## 一、实验：tree-sitter → SymbolGraph

`benchmarks/test_tree_sitter.py` 用 `tree-sitter` + `tree-sitter-language-pack`
解析真实样例，把 AST 定义节点映射为 `SymbolNode`/`SymbolEdge`（含 CONTAINS 边），
最后用 `graph.validate_edges()` 验收：

```text
--- typescript ---
  class     UserService      lines 2-10
  method    constructor      lines 4-4
  method    login            lines 5-8
  method    audit            lines 9-9
  function  createService    lines 12-14
  -> SymbolGraph: 6 nodes, 5 edges
  -> validate_edges(): 0 dangling edges — schema 对齐通过

--- go ---
  class     Store            lines 4-4
  method    Load             lines 6-8
  function  NewStore         lines 10-12
  function  Run              lines 14-17
  -> SymbolGraph: 5 nodes, 4 edges
  -> validate_edges(): 0 dangling edges — schema 对齐通过
```

要点：**schema 层零改动**。`SymbolKind`（class/function/method）、`EdgeKind`、
`SymbolNode` 的 id/module/file/lineno 契约对 TS/Go 完全够用——这正是 v0.1
把"语言相关"与"语言无关"分层设计时的回报。

## 二、真正的工作量在哪（按改造难度排序）

| 层 | 现状（Python/libcst） | TS/Go 改造点 | 难度 |
|---|---|---|---|
| **scanner 提取** | libcst 带完整类型信息（qualname、decorators、attr_bindings） | tree-sitter 只有语法树：qualname 要自己沿 AST 拼父链；decorators→TS decorators 可拿，Go 无 | 中 |
| **调用边解析** | 保守白名单：import 别名、`self.x` 绑定推断、super() 单继承、实例化 | TS：`this.x` 与 Python `self.x` 同构，**移植成本最低**；import 解析要吃 tsconfig paths / node_modules——**最大深坑**。Go：包系统扁平、无别名地狱，`pkg.Func` 直接可解析——**Go 比 TS 容易** | 高 |
| **悬空/动态推断** | `dynamic_out` → LLM 推断池（v0.2 机制语言无关） | 直接复用：JS/Go 的动态模式（duck typing、`any`、interface{}）同样产出悬空调用 | 低 |
| **入口点识别** | `app.route`/`main`/`__init__` 装饰器启发式 | TS：Express/Nest 装饰器 + `main`；Go：`func main` + `http.HandleFunc` 注册表 | 中 |
| **领域特化** | Flask/FastAPI 路由表 + Django urlconf + ORM 数据流（v0.3） | 每个框架一套：Express 路由是**运行时注册**（`app.get(...)`），静态可确认率低于 Python 装饰器；GORM/databasesql 调用形状可复用 dataflow 思路 | 高 |
| **describer/渲染/Viewer** | 语言无关（prompt view、Mermaid、时序图、RAG 检索） | **零改动**。`pruned-graph.json` 的消费者完全不知道节点是哪国语言 | 零 |
| **分析器基建** | tokenizer 用 tiktoken（与语言无关）、tokens 预算 | 零改动 | 零 |

## 三、建议路线（如果做）

1. **先 Go 后 TS**。理由：Go 的 import 解析没有 node_modules 深坑、没有
   tsconfig path alias、没有运行时对象扩展；`pkg.Func` 调用 90% 可静态确认。
   一个 Go POC（scanner 换 tree-sitter + builder 白名单复用）预计 2-3 周。
2. **TS 走"项目级"而非"库级"**：只解析 tsconfig include 范围内的源码，把
   node_modules 一律视为 EXTERNAL 节点——和 Python 对 stdlib/site-packages 的
   处理同构，坑立刻少一半。
3. **scanner 接口已经就位**：`RawSymbolBundle` 是语言中立的（symbols/imports/
   dynamic_imports/attr_bindings）。Go 的 `x := NewStore()` 恰好能映射到
   `attr_bindings` 机制——v0.2 的 self-binding 推断可以原样吃 Go 的
   `s := NewStore(); s.Load()`。
4. **多语言混合仓库**：`SymbolNode.module` 加语言前缀（`go:pkg/store`）即可
   同图共存，PageRank/枢纽/时序图/RAG 全链路无感。

## 四、诚实的边界

- tree-sitter 给的是**语法树**，不是 libcst 那种带作用域的分析。Python 版
  scanner 的许多精确性（import 别名消解、`self.x = Engine()` 类型绑定）依赖
  libcst 的作用域分析——TS/Go 版要么用各自的语义工具（tsserver / go/packages），
  要么接受更低的静态确认率、把更多东西推进 LLM 推断池。
- 悬空调用推断（v0.2）在动态语言上价值最大；Go 静态可确认率高，反而
  **不太需要**推断器——这对 Go 版是个好消息（少一个大模型依赖）。
- 本实验只验证了"定义提取 + schema 映射"。调用边、入口点、框架特化是
  真正的工程量所在，评估为 **Go ≈ 2-3 周 POC、TS ≈ 4-6 周 POC**（单人）。

## 复现

```bash
.venv/Scripts/pip install tree-sitter tree-sitter-language-pack \
    -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv/Scripts/python.exe benchmarks/test_tree_sitter.py
```
