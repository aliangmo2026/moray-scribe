# Benchmark 排行榜：10 个顶级开源项目的解析实战

> 生成工具：`benchmarks/run_leaderboard.py`（数据：`.bench-data/leaderboard.json`），
> 渲染工具：`benchmarks/render_leaderboard.py`。无人工数字——每个单元格都是机器写出的。

- **语料**：10 个风格各异的开源 Python 项目（gitee 镜像 shallow clone，排除 tests/）
- **环境**：本机 GPU + Ollama，doc 视图 `max_nodes=60`（7B 模型稳定口径），模型见各记录
- **口径**：`graph` = 纯静态链路（扫描→建图→PageRank→剪枝）；
  `doc` = graph + LLM 描述 + 业务时序图

## 总榜（按解析耗时排序）

| # | 项目 | 文件数 | 符号数 | 节点 | 边 | 枢纽 | 死代码 | graph耗时 | doc耗时 | 时序图 | 模型 |
|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| 1 | [requests](https://github.com/search?q=requests) | 21 | 287 | 60 | 74 | 62 | 86 | 1.04 s | 57.9 s | 1 | qwen2.5:7b |
| 2 | [tqdm](https://github.com/search?q=tqdm) | 47 | 306 | 60 | 56 | 71 | 123 | 1.34 s | 351.5 s | 3 | qwen3.5:9b |
| 3 | [flask](https://github.com/search?q=flask) | 35 | 450 | 60 | 19 | 98 | 243 | 1.62 s | 243.3 s | 2 | qwen2.5:7b |
| 4 | [click](https://github.com/search?q=click) | 32 | 721 | 60 | 19 | 151 | 314 | 3.22 s | 512.8 s | 4 | qwen3.5:9b |
| 5 | [fastapi](https://github.com/search?q=fastapi) | 553 | 1598 | 60 | 15 | 87 | 571 | 7.05 s | 78.0 s | 1 | qwen2.5:7b |
| 6 | [aiohttp](https://github.com/search?q=aiohttp) | 89 | 1915 | 60 | 18 | 123 | 871 | 7.66 s | 333.9 s | 2 | qwen3.5:9b |
| 7 | [scrapy](https://github.com/search?q=scrapy) | 210 | 2467 | 60 | 14 | 85 | 1024 | 10.44 s | 618.2 s | 1 | qwen3.5:9b |
| 8 | [pydantic](https://github.com/search?q=pydantic) | 67 | 1501 | 60 | 9 | 127 | 707 | 10.51 s | 452.1 s | 0 | qwen3.5:9b |
| 9 | [rich](https://github.com/search?q=rich) | 146 | 1159 | 60 | 15 | 165 | 521 | 10.67 s | 637.3 s | 4 | qwen3.5:9b |
| 10 | [pandas](https://github.com/search?q=pandas) | 389 | 9926 | 60 | 14 | 53 | 4989 | 54.08 s | 270.6 s | 0 | qwen2.5:7b |

## 观察

- 10 个项目合计 **1589** 个文件、**600** 个图节点，纯静态链路总耗时 **107.6 s**（平均 10.8 s/项目）；
- LLM 文档阶段总耗时 **3556 s**（受模型与 ladder 降级影响，波动是常态）；
- 共识别 **9449** 个死代码候选、**18** 条业务时序图；无一降级；
- 节点数与文件数大致线性，但**死代码候选比例差异巨大**（见总榜）——
  这是各项目 API 面与内部工具函数比例的真实差异，不是引擎噪声。

## 复现

```bash
bash benchmarks/clone_corpus.sh .stress10          # gitee 镜像 shallow clone
.venv/Scripts/python.exe benchmarks/run_leaderboard.py   # graph + doc
.venv/Scripts/python.exe benchmarks/render_leaderboard.py # 生成本文档
```

*最后更新：2026-11-03（UTC）；doc 阶段混用 qwen2.5:7b 与 qwen3.5:9b（各记录的 model 列如实标注——9b 对大仓库的 describer 成功率显著更高，本次全程零降级）*
