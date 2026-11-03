# [分享] 接手别人的 Python 项目先让它自己画图+自己回答"改这里会炸哪" — 全本地、开源、不传一行代码

V2EX / Reddit 极客风 · moray-scribe v0.5

## 背景

接手祖传 Python 项目的第 N 天，你面对 500 个文件没有一个 README，原作者已失联。
Cursor/Copilot 的套路是"打开一个文件问一句"——上下文里根本没有全局，
问出来的答案全是糊弄。

所以写了个工具：**先把整个项目静态分析成一张符号调用图，再让本地 Ollama
基于这张图写文档/画图/答疑**。全程 localhost，一行代码都不出门。

GitHub: https://github.com/aliangmo2026/moray-scribe （MIT，求 Star ⭐）

## 它能干嘛

```bash
pip install moray-scribe
moray-scribe doc ./你的祖传项目 --out ./arch-doc/
# → ARCHITECTURE.md：分层架构图(mermaid) + 模块职责 + 核心业务时序图
#   + 死代码候选 + PageRank 枢纽 + 已知缺口，AI 推理和引擎统计分栏标注
```

```bash
python web_viewer/serve.py out/pruned-graph.json
# → 力导向拓扑画布：拖拽/缩放/点节点看源码
# → 右下角 💬 直接问："改 login 逻辑会影响哪些模块？"
#    后端先反向 BFS 找全部调用方 + PageRank 剪枝出子图 + 切源码片段，
#    再把"问题+子图"喂给本地模型，回答里的节点点一下直接在画布高亮
```

## 技术上几个可能有点意思的点

1. **模型不写 Mermaid，引擎写**。模型只输出结构化 JSON（参与者+消息序列），
   Pydantic strict schema + 语义校验 + 引擎确定性渲染——幻觉没有生存空间。
   校验失败自动降级到引擎直绘，文档永远完整。
2. **Graph RAG 不是向量库**。检索 = 词法打分 + 反向 BFS（影响面）+ PageRank
   预算 + 源码切片，全标准库实现。对"改 X 会影响什么"这类问题，
   图结构检索比 embedding 相似度诚实得多。
3. **连 numpy 都删了**。PageRank 幂迭代手写 30 行，和 networkx 输出逐位一致，
   冷启动 1.25s → 0.15s。
4. **测试离线化**。真实模型输出录制成回放资产，`mock_ollama` fixture 起本地
   Ollama 线协议假服务；autouse 守卫让任何测试连 11434 就报错。
   CI 机器不用装 Ollama。

## 开箱即用（v0.5 新增）

```bash
docker compose up        # → http://localhost:8000，项目挂 /work，
                         # chat 走 host.docker.internal 连宿主机 Ollama
```

```powershell
# Windows 不想装 Python：
powershell -ExecutionPolicy Bypass -File install.ps1
```

## 已经在真项目上打过脸

- v0.2 压测：flask / sqlalchemy / django 三个大库，动态调用推断精确率
  92%-95%（虚线标注 `[AI推断]`，宁缺毋滥）；
- v0.5 榜单：tqdm/flask/requests/pandas/scrapy/click/rich/pydantic/fastapi/
  aiohttp 十个项目 graph+doc 全链路实测，
  数据与复现步骤：docs/benchmark-leaderboard.md（全部机器写出，无人工数字）；
- 多语言：tree-sitter 实验验证 TS/Go 能零改动映射进现有 SymbolGraph schema，
  评估文档 docs/multi-lang-future.md。

## 求喷求 Star

- 觉得"静态分析+本地模型"这条路有意思的，来个 Star ⭐
- 觉得哪里是伪需求的，来个 issue 骂醒我
- 特别欢迎拿你们公司的祖传项目跑一遍然后把 ARCHITECTURE.md 贴出来打脸

GitHub: https://github.com/aliangmo2026/moray-scribe
