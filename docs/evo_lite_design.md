# Evo-Lite Design

> **状态：Phase 3.5 已完成。** T00 ~ T07 全部实现并有通过的测试，逐项对应见第 5 节。本文件保留原始设计意图，同时补上实现与验证的落点。

## 1. 定位

Evo-Lite 是 GuardedOps 的 Phase 3.5：安全经验沉淀与自评估闭环。

它不改变执行能力，不训练模型，也不自动修改代码。它只把 GuardedOps 的真实执行结果转化为可审计的评估、反思、经验记录和安全 workflow 模板，为后续 planner 提供受控建议。

---

## 2. 硬约束

Evo-Lite 必须遵守：

- 不改模型权重；
- 不训练 LoRA / SFT / DPO / RL；
- 不接在线训练或自动强化学习；
- 不自动修改 policy；
- 不自动修改 executor；
- 不自动生成可执行脚本；
- 不创建 `run_shell_tool`、`execute_command_tool`、`bash_tool` 或其他通用执行工具；
- 经验和 workflow 只能建议 planner；
- workflow 只能调用白名单工具；
- reflection 只能写入经验，不得更改系统边界；
- 最终 allow / deny 仍由 policy engine 决定。

---

## 3. 目标产物

Phase 3.5 的目标产物是设计与受控数据结构，不是业务执行代码。

- Execution Evaluator：从执行结果、风险决策、确认状态和后置验证中生成评估记录；
- Experience Store：保存带来源、适用范围、风险等级和审计引用的经验；
- Reflection Generator：根据评估记录生成安全反思，只写入经验；
- Safe Workflow Templates：沉淀只调用白名单工具的安全 workflow 模板；
- Workflow Retrieval in Planner：让 planner 可读取 workflow 建议，但不得跳过 policy；
- Evo-Lite Orchestrator Hook：在编排边界内挂接评估和建议，不改变执行路径；
- Safety Regression Benchmark：验证 Evo-Lite 没有扩大执行能力。

---

## 4. 数据流边界

建议数据流：

1. GuardedOps 完成一次受控执行或拒绝；
2. Execution Evaluator 读取执行摘要、risk_decision、confirmation_status、tool_calls、post_check；
3. Experience Store 保存评估结果和来源引用；
4. Reflection Generator 生成安全经验；
5. Safe Workflow Templates 从重复安全路径中沉淀模板；
6. Planner 后续只把模板作为候选建议；
7. Policy engine、validators、confirmation policy 和工具白名单仍执行最终把关。

任何 Evo-Lite 输出都不能直接进入 executor。

---

## 5. 任务映射

| Task ID | 名称 | 输出边界 | 状态 | 实现与验证 |
|---|---|---|---|---|
| P3.5-T00 | 更新总控文件以加入 Evo-Lite 阶段 | 只更新总控文件和设计说明 | DONE | 本文件 + `process/` 下的总控文件 |
| P3.5-T01 | Execution Evaluator | 评估记录，不触发执行 | DONE | `app/evolution/evaluator.py`；`tests/test_evaluator.py` |
| P3.5-T02 | Experience Store | 经验记录，不作为最终安全决策 | DONE | `app/evolution/experience_store.py`；`tests/test_experience_store.py` |
| P3.5-T03 | Reflection Generator | 反思文本和经验条目，不修改边界 | DONE | `app/evolution/reflection.py`；`tests/test_reflection.py` |
| P3.5-T04 | Safe Workflow Templates | 白名单工具模板，不生成脚本 | DONE | `workflows/templates/*.json`；`tests/test_workflow_templates.py` |
| P3.5-T05 | Workflow Retrieval in Planner | planner 建议，不绕过 policy | DONE | `app/evolution/workflows.py`；`tests/test_workflow_retrieval.py` |
| P3.5-T06 | Evo-Lite Orchestrator Hook | 挂接评估和建议，不改变执行能力 | DONE | `app/evolution/init.py`；`tests/test_evo_lite_hook.py` |
| P3.5-T07 | Safety Regression Benchmark | 安全回归验证 | DONE | `benchmarks/safety_regression.json`；`tests/test_safety_regression.py` |

Phase 3.5 之后 Phase 3.6 又给 experience 加了隔离、去重和晋升门禁，见 `phase_3_6_design.md` 的 P3.6-T04。

---

## 6. 验证标准

Phase 3.5 实现必须能证明下列各点，对应的断言已经落到测试里：

| 要证明的事 | 断言它的测试 |
|---|---|
| 没有新增通用 shell 工具 | `tests/test_workflow_templates.py::test_templates_do_not_contain_shell_or_raw_command_content` |
| workflow 中只出现白名单工具 | `tests/test_workflow_templates.py::test_templates_contain_allowed_tools_and_steps_stay_within_them` |
| planner 使用 workflow 后仍会经过 policy | `tests/test_workflow_retrieval.py::test_workflow_derived_plan_does_not_execute_tools` |
| reflection 只写入经验，不改变系统边界 | `tests/test_reflection.py::test_reflections_do_not_generate_dangerous_suggestions` |
| hook 不把待确认状态改成已执行 | `tests/test_evo_lite_hook.py::test_s1_pending_confirmation_is_not_changed_to_execution` |
| 经验存储失败不影响主请求 | `tests/test_evo_lite_hook.py::test_store_write_failure_does_not_break_main_request` |
| 经验不落敏感字段或原始命令文本 | `tests/test_experience_store.py::test_sensitive_or_large_fields_are_not_saved` |
| 回归覆盖禁止 raw shell / 禁止绕过 policy | `tests/test_safety_regression.py::test_all_safety_regression_cases_pass` |

「没有新增训练依赖、没有修改模型权重」由 `pyproject.toml` 保证：运行时依赖只有 fastapi / uvicorn / pydantic / paramiko，没有任何训练框架。

### 已知缺口

`DEFAULT_TEMPLATE_DIR` 指向仓库根的 `workflows/templates/`，位于 `app` 包之外，因此不会被打进 wheel。从源码仓库运行不受影响，从 wheel 安装后调用默认模板目录会失败。见 `process/validation_matrix.md` 第 6 节。
