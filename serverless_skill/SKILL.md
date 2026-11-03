---
name: moray-scribe
version: 0.1.0
display_name: 遗留代码架构分析器
display_name_en: Legacy Code Architecture Analyzer
description_zh: 分析 Python 代码结构，提取核心枢纽（PageRank）、死代码候选、入口点等静态拓扑信息。适用于理解遗留代码、查找无用代码、分析系统核心模块。
description_en: Analyzes Python code structure to extract core hubs (PageRank), dead code candidates, and entry points. Suitable for understanding legacy code, finding unused code, and analyzing core system modules.
---

# moray-scribe (遗留代码架构分析)

## 接口说明
本技能接收一个名为 `files` 的字典对象，键为文件的相对路径（包含 `.py` 后缀），值为该文件的源码字符串。

## 输入参数 Schema
```json
{
  "type": "object",
  "properties": {
    "files": {
      "type": "object",
      "description": "包含代码文件路径和内容的字典",
      "additionalProperties": {
        "type": "string"
      }
    }
  },
  "required": ["files"]
}
```

## 调用示例
当用户提供代码时，请严格构造如下 JSON 进行调用：

```json
{
  "files": {
    "src/main.py": "def main(): pass",
    "src/utils.py": "def dead_function(): pass"
  }
}
```

## 返回结果
工具将返回一个 JSON，包含 `total_symbols`（总符号数）、`core_hubs`（核心枢纽列表）、`dead_code_candidates`（死代码候选）等信息。请根据这些数据，使用自然语言向用户解释代码的架构和冗余情况。
