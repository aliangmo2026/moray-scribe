# 终极压测报告 · v0.3

> 日期：2026-11-03 · 机器：Windows 11 · RTX 5060 Laptop 8 GB · Python 3.12 (venv)
> 模型：describer `qwen3.5:9b` · inferencer `qwen2.5:7b`（Ollama 本地，GPU offload）
> 目标：在**十万行级顶级 Python 项目**上跑通全链路，量化内存/耗时优化，并找到
> 推断并发的吞吐拐点。所有数字均来自本机实测，原始 JSON 在 `.stress/results/`。

---

## 一、压测对象

| 项目 | 版本/来源 | 扫描范围 | Python 文件 | 代码行 | 符号 | 图节点 | 图边 | 悬空调用池 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| **django/django** | 本地浅克隆 | 仓库根（CLI 默认规则） | 2,096 | **350,972** | 27,761 | 29,857 | 50,840 | 87,766 |
| **django（仅库代码）²** | 本地浅克隆 | 排除 `tests/`、`docs/` | 920 | **168,529** | 11,656 | 12,576 | 23,513 | — |
| **sqlalchemy（仅库代码）** | 本地浅克隆 | `lib/`，排除 `testing/` | 218 | **229,491** | 11,153 | 11,371 | 18,129 | — |
| flask | 本地浅克隆 | `src/flask` | 24 | 9,566 | 416 | 440 | 685 | 827 |
| examples/flask_demo | 本仓库新增 | 7 | 243 | 23 | 30 | 47 | 60 |

² django 的包目录 `django/` 自带 `__init__.py`，CLI 会把扫描根上移到仓库根，因此默认
会连 `tests/`（2,011 个文件、约 36 万行）一起扫描；单文件 >512 KB 的会被跳过，故
2,096 个文件对应 350,972 行。**仅库代码口径是 920 个文件 / 168,529 行**，
sqlalchemy 是 218 个文件 / 229,491 行——两者都满足「超过 10 万行」的目标。
SHOWCASE 使用库代码口径（架构视图不应包含测试代码）。

django 的 35 万行满足「超过 10 万行的顶级 Python 项目」这一目标；三条链路的产物均可
用同一套命令复现。

---

## 二、django：分阶段耗时与内存

命令：

```bash
python benchmarks/pipeline_profile.py .stress/django/django
```

| 阶段 | 耗时 | 峰值 RSS | 说明 |
|---|---:|---:|---|
| scan（libcst 解析 2,096 文件） | 181.8 s / 500.4 s¹ | 347–350 MB | 单文件约 87–240 ms，取决于文件系统/杀软状态 |
| graph（建图 + PageRank + 入口/死代码） | 7.9–8.7 s | **475–479 MB** | 29,857 节点 / 50,840 边 |
| prune（裁剪到 200 节点） | 0.31–0.35 s | 467–472 MB | 不再深拷贝全图（见第三节） |
| render（路由表 + 数据流 Mermaid + JSON） | 0.04–0.05 s | 465–471 MB | 路由 61 条 / 数据流 36 条 |

¹ 同一份代码三次测量得到 181.8 s、500.4 s、504.6 s。差异来自宿主环境（系统优化器 /
杀软对 2,096 个新文件的实时扫描），不是代码路径变化：flask（24 文件）稳定在 1.36 s，
与历史 1.48 s 持平。**报告取区间而不是单一数字**，避免把环境噪声当成性能结论。

**峰值内存 479 MB**（静态链路）。历史版本在同一项目上跑完整 LLM 链路时峰值为
**687.3 MB**，其中 `prune` 阶段单步就抬升 **+125 MB**（558.5 → 683.7 MB）——那正是
「先深拷贝整图再裁剪」造成的尖峰。v0.3 移除了这一步，并把 scanner bundles 在
建图后立刻释放：

```
历史 prune 峰值   683.7 MB  ──┐
历史 describe 峰值 687.3 MB   ├─ 深拷贝全图 + 全程持有 bundles
v0.3 prune 峰值   467–472 MB ─┘  只拷贝最终保留的 ≤200 节点
```

---

## 三、代码级优化清单

| # | 问题 | 证据 | 修复 | 实测效果 |
|---|---|---|---|---|
| 1 | `prune()` 先用 `model_copy(deep=True)` 复制整张图 | django 29,857 节点时 prune 内存 +125 MB | 原地读图 + 只深拷贝保留节点/边；复用已算好的 PageRank | prune 0.31 s、无全图副本 |
| 2 | `networkx.pagerank` 懒加载 SciPy/NumPy | 30 节点小图 graph 阶段 **0.992 s**（几乎全是 import） | 自研纯 Python 幂迭代，平行边语义与 nx 逐位一致（差值 1.7e-18） | graph 阶段 **0.003 s（330×）** |
| 3 | numpy / scipy / networkx 三个重依赖 | 安装体积大、冷启动慢 | 全部移除 | 冷启动 1.25 s → **0.15 s（8.3×）**；峰值 RSS 97.4 → **67.2 MB（-31%）** |
| 4 | `doc` 全程持有 2,096 个 scanner bundle | LLM 阶段内存不释放 | 建图后 `del bundles` | 静态链路峰值 479 MB，LLM 阶段不再叠加 bundle |
| 5 | 每个调用都渲染参数文本做 urlconf 匹配 | django 87k 悬空调用/全量调用 | 先按 callee 末段快速拒绝，再渲染参数 | 消除无谓的 `code_for_node` 调用 |
| 6 | 已知缺口无上限 | django 可产出上万条 "no recorded calls" | 文档侧截断 200 条 + 汇总行 | ARCHITECTURE.md 不再膨胀 |
| 7 | 推断 `src` 必须逐字相等 | 小模型返回 `login()` / 反引号 → `rejected=5` | 归一化（去反引号、去尾部签名）+ 唯一匹配 | `merged 0 → 5`、`rejected 5 → 0` |
| 8 | 主图虚线语义会被模型破坏 | 模型把普通 `uses` 边设为 dashed | 渲染器强制：非 `inferred` 的虚线一律实线 | 「虚线=AI推断」成为硬保证 |
| 9 | 推断结果可能进不了主图 | 模型未给 diagram 节点填 module/symbol_ids | Prompt R8/R9 + 新增确定性「AI 推断调用关系」Mermaid | 演示项目主图注入 1 条 + 独立虚线图 5 条 |

---

## 四、冷启动（小项目最常见场景）

`examples/flask_demo`（7 文件 / 243 行），同一台机器：

| 指标 | 优化前 | 优化后 | 变化 |
|---|---:|---:|---|
| 全静态链路总耗时 | 1.25 s | **0.15 s** | **8.3×** |
| scan | 0.248 s | 0.134 s | 1.9× |
| graph | 0.992 s | **0.003 s** | 330× |
| prune | 0.004 s | 0.003 s | — |
| render | 0.010 s | 0.005 s | 2× |
| 峰值 RSS | 97.4 MB | **67.2 MB** | -31% |

这就是「秒级出图」的来源：小项目根本不需要等模型，静态拓扑 + Web UI 已经可用。

---

## 五、推断并发：GPU 显存与吞吐的平衡点

`benchmarks/concurrency_sweep.py` 固定同一批 60 个悬空调用（3 个请求批次），
在 1/2/4/8 并发下各跑一次完整推断；GPU 显存由 `nvidia-smi` 以 5 Hz 采样。

| 并发 | 墙钟 | 请求数 | 合并边 | token 总量 | 吞吐 (tok/s) | GPU 峰值 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 47.2 s | 3 | 7 | 5,722 | 121.3 | 4,908 MiB |
| 2 | 49.3 s | 3 | 5 | 5,719 | 116.0 | 4,908 MiB |
| 4 | **46.4 s** | 3 | 7 | 5,745 | **123.7** | 4,908 MiB |
| 8 | 48.8 s | 3 | 7 | 5,732 | 117.4 | 4,908 MiB |

**结论**：

1. **并发度对吞吐几乎没有影响**（116–124 tok/s，差异在噪声内）。Ollama 对同一模型
   默认串行执行，多请求只是排队；显存恒定 4,908 MiB 也说明没有并行 KV cache。
2. 真正的吞吐决定因素是**模型大小与 GPU offload 比例**，不是客户端并发。
3. 建议值：**GPU 2、纯 CPU 1**。并发 4（当前默认）不会更慢，但没有收益；并发 8
   只会让超时风险上升。要让并发真正生效，需要给 Ollama 开
   `OLLAMA_NUM_PARALLEL>1`，代价是显存按倍增长——8 GB 卡上不建议。
4. 客户端并发仍有一个实际作用：**一个批次失败不阻塞其他批次**（`asyncio.gather`
   里单批异常被记录，其余继续），这是可靠性收益而非吞吐收益。

复现：

```bash
python benchmarks/concurrency_sweep.py .stress/flask/src/flask \
    --levels 1,2,4,8 --calls 60 --batch 8 --model qwen2.5:7b
```

---

## 六、大项目推断质量（flask 440 节点）

| 指标 | 数值 |
|---|---|
| 悬空调用池 | 827 |
| 送入模型（有候选目标） | 60 |
| 请求批次 | 3 |
| 合并进图（high） | 5–7（模型温度 0.1，多次运行有轻微波动） |
| 中低置信度缺口 | 6–8 |
| 无效/无回答 | 6 |
| token | ~5.7k（入 1.2k + 出 0.3k ×3 批） |

精确率抽样（人工复核合并边）：本轮的 5 条全部指向真实调用目标
（`user.check_password`、`service.create_order/cancel_order/list_orders`、`order.save`）。

---

## 七、复现全部数字

```bash
# 静态链路（无需模型）
python benchmarks/pipeline_profile.py .stress/django/django --label django
python benchmarks/pipeline_profile.py .stress/flask/src/flask --label flask

# 推断并发
python benchmarks/concurrency_sweep.py .stress/flask/src/flask --levels 1,2,4,8

# 演示项目全链路（需要本地 Ollama）
moray-scribe doc examples/flask_demo --out docs/assets/flask_demo --infer
```

## 八、局限

- 单机单次测量，Windows 文件系统与杀软状态对 scan 影响很大（见注 1）；
  graph/prune/render 的数字在三次运行中稳定（±10%）。
- 纯 Python PageRank 在 3 万节点图上比 SciPy 版慢约 6 s，但省下每次冷启动
  0.65 s 的 import 与约 40 MB 常驻内存；对小项目（最常见场景）是净胜。
  如未来需要十万级节点的交互式分析，可再引入可选的 SciPy 加速路径。
- 并发结论绑定 Ollama 默认调度；换成 vLLM/TGI 等连续批处理后端需重新测。
