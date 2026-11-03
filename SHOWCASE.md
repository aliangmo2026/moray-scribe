# moray-scribe SHOWCASE

> 由 moray-scribe v0.3.0 自动生成 · 图谱为**确定性静态分析**产物，
> 仅每节末尾的一句点评由本地大模型撰写。

三个顶级开源项目的逆向架构视图：每个项目只扫描、建图、聚合模块，
**不让模型参与画图**——图里的每条边都能回溯到源码。

| 项目 | 领域 | Python 文件 | 符号 | 图节点/边 | 路由 | ORM 模型 | 数据流 |
|---|---|---:|---:|---|---:|---:|---:|
| [pallets/flask](#flask) | Web 框架 | 24 | 416 | 440/685 | 0 | 0 | 0 |
| [sqlalchemy/sqlalchemy](#sqlalchemy) | ORM / 数据库工具包 | 218 | 11,153 | 11,371/18,129 | 0 | 0 | 1 |
| [django/django](#django) | 全栈 Web 框架 | 920 | 11,656 | 12,576/23,513 | 19 | 82 | 23 |

<a id="flask"></a>

## pallets/flask

**规模**：24 个 Python 文件 · 416 个符号 · 24 个模块 · 230 个死代码候选

**模块级核心架构**（按 PageRank 取前 12 个模块；箭头上的数字是聚合后的
导入/调用/继承关系数；虚线框圆角节点 = 含 ORM 模型的模块）：

```mermaid
flowchart LR
    flask_app["app<br/>42 syms"]
    flask_cli["cli<br/>41 syms"]
    flask_sansio_scaffold["scaffold<br/>36 syms"]
    flask_sansio_blueprints["blueprints<br/>37 syms"]
    flask_json_tag["tag<br/>45 syms"]
    flask_helpers["helpers<br/>23 syms"]
    flask_ctx["ctx<br/>31 syms"]
    flask_sansio_app["app<br/>34 syms"]
    flask_sessions["sessions<br/>27 syms"]
    flask_templating["templating<br/>18 syms"]
    flask_debughelpers["debughelpers<br/>12 syms"]
    flask_testing["testing<br/>16 syms"]
    flask_sansio_app -->|"5"| flask_sansio_scaffold
    flask_app -->|"4"| flask_helpers
    flask_cli -->|"4"| flask_helpers
    flask_sansio_blueprints -->|"4"| flask_sansio_scaffold
    flask_app -->|"3"| flask_sansio_app
    flask_sansio_app -->|"3"| flask_helpers
    flask_templating -->|"2"| flask_helpers
    flask_templating -->|"2"| flask_debughelpers
    flask_sansio_scaffold -->|"2"| flask_helpers
    flask_app -->|"1"| flask_ctx
    flask_app -->|"1"| flask_sessions
    flask_app -->|"1"| flask_templating
    flask_app -->|"1"| flask_testing
    flask_app -->|"1"| flask_debughelpers
    flask_cli -->|"1"| flask_app
    flask_ctx -->|"1"| flask_helpers
    flask_ctx -->|"1"| flask_app
    flask_ctx -->|"1"| flask_sessions
```

**引擎观测**

- 核心枢纽：`flask.app.Flask.ensure_sync`, `flask.app.Flask.async_to_sync`, `flask.sansio.blueprints.Blueprint.record_once`
- 静态路由：0 条 · ORM 模型类：0 个 · 确认数据流：0 条

> 💬 **AI 点评**：app 作为入口层，依赖 cli 和 blueprints

---

<a id="sqlalchemy"></a>

## sqlalchemy/sqlalchemy

**规模**：218 个 Python 文件 · 11,153 个符号 · 218 个模块 · 5,330 个死代码候选

**模块级核心架构**（按 PageRank 取前 12 个模块；箭头上的数字是聚合后的
导入/调用/继承关系数；虚线框圆角节点 = 含 ORM 模型的模块）：

```mermaid
flowchart LR
    sqlalchemy_sql_elements["elements<br/>466 syms"]
    sqlalchemy_sql_compiler["compiler<br/>409 syms"]
    sqlalchemy_sql_selectable["selectable<br/>356 syms"]
    sqlalchemy_sql_sqltypes["sqltypes<br/>271 syms"]
    sqlalchemy_sql_base["base<br/>195 syms"]
    sqlalchemy_sql_schema["schema<br/>245 syms"]
    sqlalchemy_dialects_mssql_base["base<br/>234 syms"]
    sqlalchemy_dialects_postgresql_base["base<br/>220 syms"]
    sqlalchemy_dialects_mysql_base["base<br/>157 syms"]
    sqlalchemy_ext_associationproxy["associationproxy<br/>204 syms"]
    sqlalchemy_sql_operators["operators<br/>184 syms"]
    sqlalchemy_orm_session["session<br/>148 syms"]
    sqlalchemy_sql_selectable -->|"25"| sqlalchemy_sql_base
    sqlalchemy_sql_selectable -->|"12"| sqlalchemy_sql_elements
    sqlalchemy_sql_elements -->|"11"| sqlalchemy_sql_base
    sqlalchemy_sql_schema -->|"10"| sqlalchemy_sql_base
    sqlalchemy_sql_compiler -->|"5"| sqlalchemy_sql_base
    sqlalchemy_sql_sqltypes -->|"4"| sqlalchemy_sql_base
    sqlalchemy_sql_schema -->|"3"| sqlalchemy_sql_selectable
    sqlalchemy_sql_schema -->|"3"| sqlalchemy_sql_elements
    sqlalchemy_sql_elements -->|"2"| sqlalchemy_sql_operators
    sqlalchemy_dialects_mssql_base -->|"1"| sqlalchemy_sql_compiler
    sqlalchemy_dialects_mssql_base -->|"1"| sqlalchemy_sql_elements
    sqlalchemy_dialects_mssql_base -->|"1"| sqlalchemy_sql_selectable
    sqlalchemy_dialects_mysql_base -->|"1"| sqlalchemy_sql_compiler
    sqlalchemy_dialects_mysql_base -->|"1"| sqlalchemy_sql_sqltypes
    sqlalchemy_dialects_postgresql_base -->|"1"| sqlalchemy_sql_base
    sqlalchemy_dialects_postgresql_base -->|"1"| sqlalchemy_sql_compiler
    sqlalchemy_ext_associationproxy -->|"1"| sqlalchemy_sql_base
    sqlalchemy_orm_session -->|"1"| sqlalchemy_sql_base
```

**引擎观测**

- 核心枢纽：`sqlalchemy.util.concurrency.greenlet_spawn`, `sqlalchemy.util.concurrency.await_`, `sqlalchemy.util.concurrency._safe_cancel_awaitable`
- 静态路由：0 条 · ORM 模型类：0 个 · 确认数据流：1 条

> 💬 **AI 点评**：compiler 是中枢，处理语法编译。

---

<a id="django"></a>

## django/django

**规模**：920 个 Python 文件 · 11,656 个符号 · 920 个模块 · 5,750 个死代码候选

**模块级核心架构**（按 PageRank 取前 12 个模块；箭头上的数字是聚合后的
导入/调用/继承关系数；虚线框圆角节点 = 含 ORM 模型的模块）：

```mermaid
flowchart LR
    django_db_models_expressions[("expressions<br/>278 syms")]
    django_db_models_fields["fields<br/>279 syms"]
    django_core_exceptions["exceptions<br/>35 syms"]
    django_db_models_query["query<br/>187 syms"]
    django_forms_widgets["widgets<br/>146 syms"]
    django_db_models_lookups["lookups<br/>105 syms"]
    django_forms_fields["fields<br/>128 syms"]
    django_utils_encoding["encoding<br/>15 syms"]
    django_test_testcases["testcases<br/>138 syms"]
    django_db_models_sql_query["query<br/>120 syms"]
    django_contrib_admin_options["options<br/>137 syms"]
    django_utils_safestring["safestring<br/>9 syms"]
    django_db_models_lookups -->|"3"| django_db_models_expressions
    django_forms_widgets -->|"3"| django_utils_safestring
    django_contrib_admin_options -->|"2"| django_utils_safestring
    django_db_models_expressions -->|"2"| django_core_exceptions
    django_db_models_sql_query -->|"2"| django_db_models_expressions
    django_contrib_admin_options -->|"1"| django_core_exceptions
    django_contrib_admin_options -->|"1"| django_forms_widgets
    django_db_models_lookups -->|"1"| django_core_exceptions
    django_db_models_lookups -->|"1"| django_db_models_fields
    django_db_models_lookups -->|"1"| django_db_models_sql_query
    django_db_models_query -->|"1"| django_db_models_expressions
    django_db_models_fields -->|"1"| django_db_models_expressions
    django_db_models_sql_query -->|"1"| django_core_exceptions
    django_db_models_sql_query -->|"1"| django_db_models_fields
    django_db_models_sql_query -->|"1"| django_db_models_lookups
    django_db_models_sql_query -->|"1"| django_db_models_query
    django_forms_fields -->|"1"| django_core_exceptions
    django_forms_fields -->|"1"| django_forms_widgets
    classDef database fill:#fff3e0,stroke:#f57c00
    class django_db_models_expressions database
```

**引擎观测**

- 核心枢纽：`django.core.exceptions.ImproperlyConfigured`, `django.core.exceptions.ValidationError`, `django.utils.module_loading.import_string`
- 静态路由：19 条 · ORM 模型类：82 个 · 确认数据流：23 条

> 💬 **AI 点评**：expressions 是表达式层，支持 ORM 查询

---

## 复现方式

```bash
# 1) 只做确定性拓扑（无需模型，秒级）
moray-scribe graph path/to/project --out out/

# 2) 交互式查看同一张图
python web_viewer/serve.py out/pruned-graph.json

# 3) 重新生成本文件（需要 .stress/ 下的三个克隆）
python benchmarks/showcase.py --projects flask,sqlalchemy,django
```

*生成工具：moray-scribe v0.3.0 · 数据源：本地浅克隆的官方仓库*
