# moray-scribe 真实项目验证报告

> 验证日期：2026-10-28 · 工具版本：moray-scribe v0.1.0 (phase-6) ·
> 真实模型：`qwen2.5:7b`（本机 Ollama） · 目标：`psf/requests@main`（浅克隆）

## 项目信息

- **项目**：`psf/requests` 的 `src/requests/` 目录
- **规模**：19 个 `.py` 文件，6394 行
- **选择理由**：
  1. 行数落在 500–20000 的目标区间，能在消费级硬件上完整跑通；
  2. 无重型原生依赖（纯 Python），`libcst` 可直接解析；
  3. 是业界公认的"成熟但有历史包袱"的库——异常体系、Mixin 继承、
     `@overload` 重载、大量内部工具函数，正好压测 scanner/grapher/describer；
  4. 不属于 moray 系列自身代码，避免自我循环验证。

## 阶段 1：扫描（`scribe_scan`）

- **文件数**：19
- **符号数**：300（经 phase-6 的 keep-last 去重后；去重前 `auth.py` 的
  `@overload` 重载会触发 duplicate-id 崩溃，见问题 1）
- **解析失败**：无（`libcst` 全部成功解析）

> 说明：`requests/auth.py` 为 `HTTPBasicAuth.__init__` / `HTTPDigestAuth.__init__`
> 各写了 3 个 `@overload` 存根 + 1 个实现，scanner 原样产出多个同 qualname 符号，
> 导致 bundle 校验报错。已在 `extractor` 加 keep-last 去重解阻（问题 1）。

## 阶段 2：图分析（`scribe_graph`）

- **节点 / 边**（`max_nodes=200` 裁剪后）：200 节点 / 370 边
- **核心枢纽（PageRank Top-10）与人工核对**：

| 排名 | 符号 | PageRank | 人工判定（是否 requests 的核心） |
|---:|---|---:|---|
| 1 | `requests.exceptions.RequestException` | 0.0531 | ✅ 全库异常基类，被继承/引用最多 |
| 2 | `requests.exceptions.RequestException.__init__` | 0.0470 | ✅ 基类构造，各子类经 `super()` 触达 |
| 3 | `requests._internal_utils.to_native_string` | 0.0160 | ✅ 编码工具，被 sessions/utils 广泛调用 |
| 4 | `requests.api.request` | 0.0139 | ✅ 顶层 `requests.get/post` 的实际入口 |
| 5 | `requests.utils.to_key_val_list` | 0.0102 | ✅ header/data 归一化核心工具 |
| 6 | `requests.structures.CaseInsensitiveDict` | 0.0100 | ✅ Header 容器，in/out 度都高 |
| 7 | `requests.exceptions.InvalidURL` | 0.0093 | ✅ 高频抛出的校验异常 |
| 8 | `requests._types.BaseRequestKwargs` | 0.0087 | ✅ 请求参数 TypedDict，广为引用 |
| 9 | `requests.cookies.create_cookie` | 0.0082 | ✅ cookie 构造核心 |
| 10 | `requests.compat._resolve_char_detection` | 0.0081 | ⚠️ 兼容层工具，偏内部但仍被引用 |

**结论**：PageRank 挑出的枢纽与 requests 的真实结构高度吻合（异常基类、主入口、
Header 容器、编码工具）。**拓扑分析准确。**

## 阶段 3：架构文档（`scribe_doc`）

- **严格管线结果**：**拒收**。`qwen2.5:7b` 生成的草稿在三层防幻觉下被拒，
  4 次重试均未通过。拒收原因（见问题 4）：
  1. `diagrams[0].kind = "classDiagram"`（R5-1 要求首图为 flowchart）；
  2. `MermaidNode` 多出 `dependencies` 字段（`extra="forbid"` 拒绝）；
  3. `metadata.total_symbols/total_edges` 用了数字而非字符串。
- **模型内容本身质量**：节点按**模块/子系统**聚合（`ExceptionsModule` /
  `CookiesModule` / `AdaptersModule` / `ModelsModule` / `SessionsModule` …），
  `symbol_ids` 引用真实符号（`requests.exceptions.HTTPError` 等），边是真实依赖
  （`Sessions→Models`、`Auth→Exceptions`）。**聚合粒度与引用准确性良好。**
- **为完成渲染评审**：将模型输出的 `kind` 强制为 flowchart、剔除多余
  `dependencies` 字段、`metadata` 值转字符串后，用 `draft_to_markdown` 渲染出
  `ARCHITECTURE.md`（268 行）供人工审读。**未改动产品代码，仅演示渲染路径。**

### 主架构图（flowchart）评审

- **节点数**：9（subsystem 级）；输入符号 45（`max_nodes=45` 裁剪后）；
  **压缩比 9/45 = 0.20**（相对全量 300 符号则为 0.03）。
- **节点是否体现"子系统"而非"类"**：✅ 是。9 个节点是 `Adapters/Auth/Compat/
  Cookies/Core/Exceptions/Help/Sessions/Utils` 模块，没有 1:1 映射到类。
- **边是否真实（抽查 3 条）**：
  1. `CoreModule → SessionsModule`（`requests/__init__.py` 组装 Session）✅ 真实；
  2. `AuthModule → ExceptionsModule`（auth 抛 `RequestException` 族）✅ 真实；
  3. `SessionsModule → CookiesModule`（Session 持有 cookie jar）✅ 真实。
- **是否有明显幻觉**：节点/边无编造；但模型给节点加了 schema 外的
  `dependencies` 字段、把首图画成 classDiagram（属**格式违规**而非内容幻觉）。

### 模块业务说明评审（抽样 3 个）

| 模块 | 描述 | 判定 |
|---|---|---|
| `requests.sessions` | "Manages session state and cookies." | 偏**符号转述**，业务洞察薄 |
| `requests.adapters` | "Manages HTTP adapters." | 同上，一句话概括 |
| `requests.compat` | "Handles compatibility issues." | 同上 |

模块说明短小、正确但**缺乏业务叙事**（未解释"为什么分层""谁持有状态"）——
受 `qwen2.5:7b` 能力限制，属"业务洞察弱"。

## 阶段 4：Mermaid 渲染

- **渲染成功**：✅ 是（`diagram-00-flowchart.mmd`）
- **产物**：合法 `flowchart TD`，9 个圆角节点 + 8 条 `-->|"depends_on"|` 边，
  语法可直接粘贴 mermaid.live 渲染（节点 id 均为合法 ASCII 标识符，label 无特殊字符）。
- **描述**：一个以 `CoreModule` 为中心、各业务模块指向 `ExceptionsModule` 的依赖图，
  层次清晰、无孤立节点。

## 质量评分（人工打分）

| 维度 | 得分 | 理由 |
|---|---:|---|
| 拓扑准确性 | 4/5 | PageRank 枢纽与 requests 真实结构高度吻合；AI 依赖边抽查全部真实。扣分：`CoreModule→一切` 的扇出略泛化。 |
| 业务洞察 | 2/5 | 模块说明多为一句话符号转述，缺"为什么/谁持有状态/如何分层"的叙事。 |
| 可读性 | 4/5 | ARCHITECTURE.md 结构完整、AI/引擎分区清晰、flowchart 干净。 |
| 无幻觉 | 3/5 | 节点/符号引用真实、无编造符号；但 known-gaps 段 38 条中大量"内部基类误报为 external"的噪声（问题 5）。 |
| **综合** | **3/5** | 管线与引擎统计扎实，模型语义层（洞察、格式遵从、缺口去噪）待加强。 |

## 发现的问题（按严重度）

1. 【严重】**scanner 在真实代码上崩溃**：`@overload` 重载 / 重定义产生重复
   symbol id，`RawSymbolBundle` 校验直接抛 `ValidationError`，`requests/auth.py`
   无法扫描。已在 `extractor._extract_source` 加 keep-last 去重热修。
2. 【严重】**OllamaClient 无输出 token 上限**：`format="json"` 下小模型会循环
   生成直到超时（实测 7b/1.5b 均 >400s 无产出）。已在 `_chat_once` 的 options
   加 `num_predict=4096` 热修；加后 7b 一次生成仅 ~13s。
3. 【一般】**默认 `request_timeout_s=120` 对 7B 大草稿过短**（问题 2 的下游表现）；
   纯加大超时无用，根因是缺输出上限。
4. 【一般】**模型不遵从严格 Schema + R5-1**：`qwen2.5:7b` 稳定把 classDiagram 放
   `diagrams[0]`（违反 R5-1）、给 `MermaidNode` 加 `dependencies`、`metadata` 用
   数值。4 次重试不改 → doc 阶段永不完成。**根因**：R5-1 只在 `_post_validate`
   拒收、**未写进 prompt**，模型首次无从知晓；重写 prompt 也未明确"节点不得含
   dependencies / metadata 值必须字符串"。
5. 【一般】**`detect_known_gaps` 误报内部基类为 external**：用裸基类名
   （`RequestException`）比对全 id 节点集（`requests.exceptions.RequestException`），
   恒不命中 → requests 的 38 条缺口里大量是内部继承被误判，噪声淹没真缺口
   （`ValueError`/`IOError` 等真外部基类）。
6. 【轻微】**附录统计基于裁剪后的图**：`ARCHITECTURE.md` 附录"分析文件数 14 /
   模块数 2"取自 `max_nodes=45` 的裁剪图，与真实 19 文件/19 模块不符，可能误导读者。

## 改进建议

**对 prompt 的**
- 把 R5-1 写进 SYSTEM/USER prompt：`diagrams[0].kind MUST be "flowchart"`，
  classDiagram 只能作 `diagrams[1:]` 细节图——让模型首次就合规，而非靠重试撞。
- 在 prompt 里显式列 MermaidNode 白名单字段（无 `dependencies`）与
  `metadata` 值必须字符串，降低 schema 违规重试。
- 要求模块说明给出"业务意图 + 依赖原因"，而非复述模块名，提升业务洞察。

**对 pruner 的**
- doc 附录统计改用**全量图**（裁剪前）计算文件数/模块数，避免低估项目规模。
- 裁剪时尽量保留 MODULE 节点（当前 45 节点里只剩 2 个模块节点）。

**对 renderer / describer 的**
- `detect_known_gaps` 先把裸基类名解析成全 id 再判外部（或直接读 builder 的
  INHERITS 边是否已解析），消除内部基类误报。
- known-gaps 在 markdown 里可设上限（如 50 条）并标注"共 N 条"，避免长噪声段。
