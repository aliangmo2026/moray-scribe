# 我们用 libcst + 自研 PageRank，让大模型给 35 万行遗留代码画架构图（附 10 项目实测榜单）

> 掘金硬核技术风 · moray-scribe v0.5 技术解析。全文无营销话术，每一个设计决策
> 都对应一次真实的翻车。

## TL;DR

moray-scribe 是一个开源的**遗留代码逆向文档引擎**：静态分析提取全项目符号图，
本地大模型只做业务推理，一次性产出「分层架构图 + 模块说明 + 核心业务时序图 +
死代码/枢纽统计」的 ARCHITECTURE.md，外加一个零依赖的交互式拓扑查看器和
Graph RAG 问答。AI 推理与引擎统计**严格分区**——你能明确知道每一句话该不该信。

本文讲三个硬核问题：

1. 为什么静态分析必须用 libcst 而不是 ast？
2. 为什么自研 PageRank（连 numpy 都不要）？
3. 怎么让大模型**零幻觉**地画图？

## 一、libcst：不是装 X，是保命

Python 逆向分析的第一反应是 `ast` 标准库。但 `ast` 丢了三样东西：

- **精确到列的源码位置**——想还原"这个调用在第几行"做悬空调用定位，ast 的
  lineno 粒度不够；
- **保真注释与格式**——提取装饰器原始文本时 ast 只给 AST 节点；
- **有损解析的健壮性**——遗留代码常有语法瑕疵，libcst 有容错模式，ast 直接炸。

更关键的是 libcst 的**作用域分析**。我们的 scanner 要把 `self.engine.run()`
解析成跨模块调用边，靠的是记录 `self.engine = Engine(...)` 这类属性绑定
（`attr_bindings`），再在建图阶段做类型推断。这需要"作用域内赋值可见"，ast
手写作用域分析等于重造 libcst。

代价也诚实说：libcst 慢于 ast。35 万行的 django（2096 个非测试文件）扫描+
建图峰值 479MB、静态链路分钟级——对"一次性生成文档"的工具可接受。

## 二、自研 PageRank：为一个 0.65 秒的 import

v0.2 时核心枢纽排序用的是 `networkx.pagerank`。压测发现一个尴尬的事实：
**`import networkx` 本身要 0.65s、40MB RSS**——对"7 个文件的小项目冷启动
1.25s"的工具来说，一个只用来跑幂迭代的重依赖成了成本大头。

幂迭代本身只有 30 行。自研版有三个必须对齐 networkx 的细节：

1. **平行边 last-write-wins**（DiGraph.add_edge 语义），否则排名会漂移；
2. **悬挂节点质量均摊**，和 networkx 幂迭代一致；
3. **收敛判据 `sum|Δ| < N·tol`**，照抄。

结果：与 networkx 输出逐位一致（1.7e-18 量级差异），冷启动降到 0.15s。
教训：**依赖不是免费的，尤其在你只需要它 5% 功能的时候。**

## 三、让大模型零幻觉画图：三层防幻觉 + 章节分区

大模型看全项目画架构图，必然幻觉。我们的方案是**让模型没有机会幻觉**：

- **L1 结构层**：模型输出必须过 Pydantic 严格 schema（`extra="forbid"`），
  节点 id 必须 `^[A-Za-z_][A-Za-z0-9_]*$`，边的端点必须在节点表里；
- **L2 提示层**：prompt 只喂引擎算好的紧凑图谱（hub 标记、路由、ORM 标签），
  模型拿到的是"事实卡片"不是源码；
- **L3 语义层**：后校验（模块 ∈ 全图、symbol_ids ⊆ 节点、摘要 ≥50 字、
  首图必须 flowchart、大图强制降密度）。

v0.4 的时序图更进一步：**模型连 Mermaid 都不写**。它输出结构化的
participants + messages，引擎校验后确定性渲染 `sequenceDiagram`——
模型给业务命名，引擎保证语法为真。校验不过就自动降级：链路收缩阶梯
（全部→一半→三分之一，带纠错压力重试），全败时引擎直绘调用链时序兜底。
文档里每一节都标注【AI 推理】或【引擎统计】——**读者永远知道该信哪部分**。

## 四、10 个顶级项目实测

v0.5 用 10 个风格各异的开源项目（tqdm/flask/requests/pandas/scrapy/click/
rich/pydantic/fastapi/aiohttp）跑了完整榜单：

- 每个项目的解析耗时、节点/边数、核心枢纽、死代码候选、业务时序图数量；
- **全部机器写出**：`run_leaderboard.py` 产出 JSONL，`render_leaderboard.py`
  渲染文档，没有一个人工数字；
- 复现三条命令：`clone_corpus.sh` → `run_leaderboard.py` → `render_leaderboard.py`。

榜单见 [benchmark-leaderboard.md](../benchmark-leaderboard.md)。

## 五、工程底线

- **离线测试**：真实模型输出录制成回放资产，`mock_ollama` fixture 起本地
  Ollama 线协议回放服务器；autouse 守卫让任何测试连真实 Ollama 端口就报错。
  没有 Ollama 的 CI 机器照样全绿。
- **Docker 开箱**：`docker compose up` → 拓扑 UI + Graph RAG 问答，
  模型走宿主机 Ollama，代码不出容器。
- **确定性**：同一输入两次运行，ARCHITECTURE.md 与 .mmd 字节一致
  （无时间戳、无随机）——测试里有哈希断言锁着。

## 结语

逆向文档这件事，难点从来不是"调大模型 API"，而是**怎么让模型只能在
静态分析画好的格子里写字**。格子画得越细，幻觉越无处容身。

GitHub：[aliangmo2026/moray-scribe](https://github.com/aliangmo2026/moray-scribe)
觉得有用的话一个 Star 就是最好的鼓励。
