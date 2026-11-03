# 今晚作战日志 · v0.3

> 任务：把 moray-scribe 从 CLI 工具推进到「可交互、领域感知、压测有数据、物料能出圈」的产品。
> 执行方式：自主设计 → 实现 → 测试 → 真实项目验证，全程不打断。
> 环境：Windows 11 · Python 3.12 venv · RTX 5060 Laptop 8 GB · Ollama 本地
> （describer=`qwen3.5:9b`，inferencer=`qwen2.5:7b`）。

---

## 00 · 基线侦察

- 全量测试 **283 passed / 16.5 s**，确认改动前是干净的。
- 本地 Ollama 在线，`.stress/` 下 django / sqlalchemy / flask 三个克隆还在，
  压测不用重新联网（本机到 CDN 是断的，后面所有前端资产因此必须零依赖）。
- 关键代码结构确认：scanner（libcst）→ grapher（保守建图）→ describer（LLM）
  → renderer（Mermaid/Markdown），三层边界清晰，适合在 scanner/grapher 加领域逻辑。

---

## 01 · 战略任务 2：领域特化（先做，因为 Web UI 要用它的字段）

### 设计

- **路由**：新增 `scanner/domains.py::parse_route_decorator`，纯函数、可单测。
  只认「HTTP 动词装饰器 + 以 `/` 开头的字符串路径」：
  `@app.route('/login', methods=['GET'])`、`@router.get('/x')`、
  `@api_view([...])`（DRF，但无路径 → 不产出 RouteInfo）。
  这样 `@cache.get('key')` 这类同名但不是路由的装饰器不会误报。
- **Django 的路径不在装饰器上**，而在 `urls.py`。补了 `UrlPatternRef` +
  `grapher/urlconf.py`：解析 `path('login/', views.login)` /
  `path('books/', BookView.as_view())`，经 import 别名解析回具体视图节点，
  挂上 `RouteInfo(methods=["ANY"])`。装饰器路由优先，urlconf 不覆盖。
- **ORM**：类体里出现 `__tablename__`、`Column/mapped_column/relationship`、
  或 Django 字段构造器（`models.CharField`）→ 打 `orm_model` 标签；
  继承 `models.Model` / `db.Model` 也算。**故意排除 pydantic 的 `Field`**，
  否则每个 schema 类都会被误判成数据库表。
- **数据流**：`grapher/dataflow.py`。只有静态可确认时才发 `DATA_FLOW` 边：
  `Model.query.get()`（接收者解析到图内类）、`self.session.commit()`
  （属性类型可追踪）、已解析调用链上的 `save/commit/delete`。
  `repo.save()`（参数类型未知）**不猜**，留给 `--infer`。
- Prompt 图例增加 `r`（路由）/ `o`（ORM）/ `df`（数据流）；
  `ARCHITECTURE.md` 新增「七、API 路由表」「八、数据流向图」（确定性生成，
  粗箭头 `==>`，ORM 模型画成数据库节点）。

### 验证

自建 `examples/flask_demo/`（7 个文件、真 Flask + SQLAlchemy + pydantic）：

```
routes: 8   (GET /health, GET/POST /login, POST /logout, POST /register,
             GET/POST /orders, GET/DELETE /orders/<int:order_id>)
orm:    2   (models.User, models.Order)
flow:   4   (auth.login→User, auth.register→User, orders.get_order→Order,
             services.create_order→User)
pydantic schemas: 0 误报 ✅
```

### 踩坑

1. **Django DRF 装饰器没有路径**：`@api_view(['GET'])` 里根本没有 URL。
   第一版测试断言它能产出 RouteInfo，属于设计错误——已改为「返回 None」，
   真正的映射交给 urls.py 解析（见上）。
2. **pydantic Field 误报**：最初把 `Field` 放进 ORM 字段集合，`schemas.py`
   整个被标成 ORM。移除并补了回归测试。
3. **测试里模块名默认值写错**（`_bundle` 默认 `api`，断言写 `app`），
   一次挂了 6 个用例，纯测试 bug。
4. **Lint/工具链插曲**：Bash 里 heredoc 写测试文件时 `\n` 被吞，导致语法错误；
   后续统一用 Edit/Write 落盘（也被安全钩子挡了一次硬编码 `secret_key`，
   改成读环境变量——这本来就是正确写法）。

---

## 02 · 战略任务 1：Web UI（零依赖，因为没网）

### 实现

`web_viewer/`：

- `index.html` / `styles.css` / `app.js`：Canvas 2D 手写力导向图。
  没有 Cytoscape/D3/CDN——本机到 jsdelivr 直接 000，任何 CDN 方案都会白屏。
- `serve.py`：纯标准库 HTTP 服务，三个接口：`/api/config`、`/api/graph`、
  `/api/source`（路径必须落在扫描根内，防目录穿越）。
- 交互：拖拽节点、空白平移、滚轮缩放、单击选中、双击居中、搜索高亮、
  按类型筛选、隐藏死代码、导出 PNG、`F/R/Esc/` 快捷键。
- 视觉：静态边灰色实线；`[AI推断]` 边**红色流动虚线**；`data_flow` 橙色粗箭头；
  路由节点青色外环；ORM 模型橙色虚线环；节点大小 = PageRank + 度数。
- 侧边栏：文档/签名、PageRank/入度/出度/复杂度、路由徽章、ORM 徽章、
  AI 推断关系（含置信度与理由）、调用/被调用列表（可点击跳转）、源码片段。
- 深链：`?select=<id>&settle=N&hide_dead=1&q=...`，可分享某个符号的视图。

### 踩坑

1. **首屏布局飞到左上角**：headless 环境里 `requestAnimationFrame` 被严重节流，
   力导向只跑了 3 帧，`needsFit` 的延迟自适应永远等不到。
   修复：布局完成后立即 `fitToView(false)`，再在模拟收敛后再 fit 一次。
2. **诊断困难**：IAB 浏览器后端 `guest not attached`，`--dump-dom` 也拿不到输出。
   加 `?debug=1` 运行时状态面板（transform/alpha/ticks/errors），
   用 `--screenshot` 直接读面板定位问题。这个开关留在了产品里，便于远程排障。
3. **中文在终端 GIF 里变方框**：Consolas 没有 CJK 字形。
   按行检测 CJK，切到微软雅黑；同时把窗口加高 720px，避免最后一行被状态栏盖住。

---

## 03 · 战略任务 3：极限压测与性能自愈（进行中）

### 已定位并修复的性能问题

| 问题 | 证据 | 修复 | 效果（flask_demo 全静态链路） |
|---|---|---|---|
| `prune` 先深拷贝整张图再裁剪 | django 39k 节点时相当于峰值翻倍 | 只拷贝最终保留的 ≤200 节点；PageRank 复用 | 大图内存峰值预计 -40%（见 ultimate-benchmark） |
| `nx.pagerank` 懒加载 SciPy | 30 节点小图 graph 阶段 **0.992 s** | 纯 Python 幂迭代，逐位复刻 last-write-wins 平行边语义（与旧值差 1.7e-18） | **0.003 s（330×）** |
| 重依赖 numpy/scipy/networkx | 安装体积 + 冷启动 | 全部移除 | 冷启动总耗时 **1.25 s → 0.15 s（8.3×）**；峰值 RSS **97.4 → 67.2 MB（-31%）** |
| `doc` 模式全程持有 1849 个 scanner bundle | 大项目 LLM 阶段内存不释放 | 建图后 `del bundles` | 需要在 django 上实测 |
| 已知缺口无上限 | django 可产出上万条 "no recorded calls" | 文档侧截断到 200 条 + 汇总行 | ARCHITECTURE.md 不再膨胀 |

### 并发调优

- `doc` 新增 `--infer-concurrency / --infer-batch-size / --infer-max-calls`，
  便于按显存档位调参。
- `benchmarks/concurrency_sweep.py`：固定同一批悬空调用，跑 1/2/4/8 并发，
  记录墙钟、token/s、合并数、GPU 显存峰值（结果见 ultimate-benchmark.md）。

### 推断质量自愈

演示项目第一次跑 `--infer`：`merged=0 rejected=5`。
抓原始响应发现模型把签名写进了 `src`：

```json
{"src": "flask_demo.auth.login()", "dst": "flask_demo.models.User.check_password", ...}
```

解析器要求逐字相等 → 5 条全废。修复：`_normalize_symbol_ref` 去掉反引号与
尾部 `(...)`，再做「精确 → 归一化唯一匹配」；`dst` 同样归一化。
重新运行：**`merged=5 gaps=0 rejected=0`**，且 `selected` 改为「真正送进模型的
调用数」（40 个悬空调用里只有 5 个有候选目标，之前统计虚高）。

### 主图虚线映射缺口

第二次运行：图里已有 5 条 `inferred` 边，但 `drew 0 inferred edge(s) into diagrams`。
原因：LLM 输出的大图节点既没有 `module` 也没有 `symbol_ids`，引擎无法把符号
映射到子系统节点。两手修复：

1. Prompt 新增 R8/R9：每个图节点必须带至少一个 `symbol_ids`、`module` 必填；
   `dashed` 仅限 `inf=1` 的边。
2. `ARCHITECTURE.md` 新增**确定性**的「AI 推断补全的调用关系」Mermaid 小节：
   直接从合并边列表渲染，永远存在、永远带 `[AI推断]` 虚线，不依赖模型排版。

---

## 04 · 战略任务 4：演示物料

- `benchmarks/make_demo_gif.py`：用 Pillow 把**真实终端输出**渲染成 GIF
  （vhs/asciinema 本机都没有，且不能联网安装）。产出 `docs/assets/demo.gif`，
  104 帧 / 1.8 MB，中文按行切换字体。
- `benchmarks/showcase.py`：对 flask / sqlalchemy / django 做确定性模块级
  架构聚合（Top-12 模块 + 聚合关系计数），再让本地模型写**一句话**点评；
  图完全由引擎生成，模型不参与画图。
- README 视觉改造：Web UI 截图、动态推断虚线图、GIF 放首屏。

---

## 05 · 收盘状态

### 交付物

| 任务 | 产物 | 状态 |
|---|---|---|
| 1 · Web UI | `web_viewer/`（HTML/CSS/JS/serve.py/README）、`docs/assets/web_viewer_demo.png` | ✅ |
| 2 · 领域特化 | `scanner/domains.py`、`grapher/urlconf.py`、`grapher/dataflow.py`、路由表/数据流文档章节、`examples/flask_demo/` | ✅ |
| 3 · 极限压测 | `docs/ultimate-benchmark.md`、`benchmarks/pipeline_profile.py`、`benchmarks/concurrency_sweep.py` | ✅ |
| 4 · 演示物料 | `docs/assets/demo.gif`、`SHOWCASE.md`、README 首屏改造 | ✅ |

### 最终验收

- **测试**：283 → **345**（新增 62 个；全绿，`python -m pytest tests -q`，ruff 全过）。
- **静态链路（django）**：
  - 默认 CLI 口径（仓库根，含 tests）：2,096 文件 / **350,972 行** / 27,761 符号，
    scan 182–500 s（环境相关）、graph 7.9–8.7 s、prune 0.31 s、render 0.04 s；
  - 库代码口径（排除 tests/docs，SHOWCASE 使用）：920 文件 / **168,529 行** /
    11,656 符号 / 12,576 节点 / 23,513 边；
  - sqlalchemy 库代码：218 文件 / **229,491 行** / 11,153 符号。
- **峰值内存**：django 静态链路 **479 MB**（历史完整链路 687 MB，prune 深拷贝尖峰 +125 MB 已消除）。
- **冷启动（7 文件示例）**：1.25 s → **0.15 s**（8.3×），内存 97.4 → **67.2 MB**（-31%）。
- **推断并发**：1/2/4/8 → 121/116/124/117 tok/s，GPU 恒定 4,908 MiB，
  结论是 **Ollama 单模型串行**，客户端并发只影响排队不影响吞吐；建议 GPU=2、CPU=1。
- **推断质量**：flask 440 节点，60 个送审调用，合并 5–7 条，人工复核全部正确；
  演示项目 `merged=5 / rejected=0`，主图成功注入 `[AI推断]` 虚线。
- **领域识别（django 库代码）**：19 条路由、82 个 ORM 模型、23 条确认数据流；
  flask_demo：8 条路由、2 个 ORM 模型、4 条数据流。
- **serverless skill**：`build_zip.py` 重建 45.1 KB，`test_handler.py --zip` 自测通过。

### 尚未做 / 下一步

1. **主图注入率**：prompt 已要求每个节点带 `symbol_ids/module`，但 9B 模型仍只让
   5 条推断边中的 1 条映射进主图。确定性「AI 推断调用关系图」已兜底；
   下一步可做「符号 → 子系统」的相似度反查（不依赖模型输出字段）。
2. **超大图 PageRank**：纯 Python 在 3 万节点上约 6 s；如需十万节点交互分析，
   可加一个可选的 SciPy 加速路径（不影响默认零依赖）。
3. **scan 内存**：bundles 与 graph 同时驻留（479 MB）。两遍扫描 + 磁盘序列化
   可再降，但会牺牲时间；暂不引入。
4. **Web UI 虚拟化**：当前 O(n²) 斥力在 ≤2,000 节点内流畅；更大图需要 Barnes-Hut。

### 复现今晚全部结果

```bash
python -m pytest tests -q
python benchmarks/pipeline_profile.py .stress/django/django
python benchmarks/concurrency_sweep.py .stress/flask/src/flask --levels 1,2,4,8
python benchmarks/showcase.py --projects flask,sqlalchemy,django
python serverless_skill/build_zip.py && python serverless_skill/test_handler.py --zip
python web_viewer/serve.py docs/assets/flask_demo/pruned-graph.json
```

