# moray-scribe v0.1.0 · 自我剖析（部分）

⚠️ 本产物**尚未完整产出**。原因：

- 本机 CPU 推理速度实测约 **2.5 tok/s**（qwen2.5:7b，纯 CPU；
  `llama-server` / Defender / IDE 等进程同时占用 CPU）
- moray-scribe 自身约 **4325 行 Python**（33 个文件，`src/moray_scribe/`），
  80 节点 / 132 边的架构草稿预估需生成 3000–4000 token，即 **约 1200–1600 秒**
- 本轮已尝试 **1 次严格管线**
  （`MORAY_SCRIBE_REQUEST_TIMEOUT_S=900 moray-scribe doc src/moray_scribe/`），
  ReadTimeout。此前 phase-6/7 在 `psf/requests` 上另有 4 次尝试，同样均
  ReadTimeout（908s / 909s / 402s / 706s）。

这是 **v0.1.0 已知的硬件约束**，非产品缺陷：

- 产品链路完整性：见 [`docs/realworld-report.md`](../../docs/realworld-report.md)
  （phase-6 已完成，qwen2.5:7b + `psf/requests` 在空闲机器上 74 秒产出合法草稿）
- 代码测试覆盖：241 个单测全绿（含异常分类、超时、CLI 覆盖）
- 待硬件恢复（GPU / 空闲 CPU / 更快模型）后，本目录将补充完整产物

## 如何复现（硬件就绪后）

```bash
export MORAY_SCRIBE_MODEL_DESCRIBER=qwen2.5:7b
export MORAY_SCRIBE_MAX_OUTPUT_TOKENS=4096
export MORAY_SCRIBE_REQUEST_TIMEOUT_S=900
export MORAY_SCRIBE_MAX_NODES=80
moray-scribe doc src/moray_scribe/ --out examples/moray-scribe-v0.1.0/
```

成功后本目录将包含 `ARCHITECTURE.md`（严格管线原生输出，零后处理）与
`diagram-XX-*.mmd`。失败时的错误信息现在会明确区分"服务不可达"与
"生成超时"（见 v0.1.0 CHANGELOG），并给出可操作建议。
