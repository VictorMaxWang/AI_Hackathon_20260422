# Phase 3.6 Design

> **状态：Phase 3.6 已完成。** T00 ~ T08 全部实现并有通过的测试，逐项对应见第 3 节。本文件保留原始设计意图，同时补上实现与验证的落点。

## 1. 定位

Phase 3.6 是 GuardedOps 在 P0 ~ P3.5 之后的下一轮优化阶段：可信控制面、证据层与鲁棒闭环。

这一阶段的重点不是继续扩工具面，也不是扩大危险能力，而是提升 GuardedOps 在解释、确认、恢复、回归和控制面展示上的可信度、可审计性与可复现性。

当前聚焦方向：

- 安全解释与证据链；
- 确认绑定与执行闭包；
- 连续任务鲁棒性与断点续跑；
- 失败恢复建议；
- 经验治理；
- 可重放安全回归；
- 可视化可信控制面。

---

## 2. 硬约束

Phase 3.6 必须继续服从 GuardedOps 的既有安全边界：

- 不开放 arbitrary shell；
- 不开放 raw command mode；
- 不让 explanation / memory / workflow / reflection 绕过 policy；
- 不自动修改 policy / executor / 风控边界；
- 不自动生成可执行 shell 脚本；
- confirmation 必须绑定执行闭包；
- evidence 必须优先来自 trace / state assertion / policy events，而不是自由叙述；
- workflow 只能调用白名单工具；
- experience 必须有隔离、去重、晋升门禁；
- benchmark 必须支持回归和重放。

Phase 3.6 只增强可信控制与证据闭环，不扩大执行面，不改变系统能力边界。

---

## 3. 任务映射

Phase 3.6 的 T00 ~ T08 已全部实现，下表的「实现」列是可以直接打开的文件，「验证」列是可以直接跑的测试。

| Task ID | 名称 | 设计目标 | 状态 | 实现 | 验证 |
|---|---|---|---|---|---|
| P3.6-T00 | 更新总控文件并加入 Phase 3.6 | 把 Phase 3.6 正式写入总控体系 | DONE | 本文件 + `process/` 下的总控文件 | — |
| P3.6-T01 | Evidence Layer Schema & Explanation Card Backend | 统一解释卡与证据层 schema，约束证据来源 | DONE | `app/models/evidence.py` | `tests/test_evidence_layer.py` |
| P3.6-T02 | Guarded Confirmation Token & Scope Binding | 让 confirmation token 绑定执行闭包、作用域与风险等级 | DONE | `app/agent/confirmation.py` | `tests/test_confirmation_token.py` |
| P3.6-T03 | Step Contracts, Drift Revalidation & Checkpoint Resume | 为连续任务建立 step contract、漂移重校验与断点续跑约束 | DONE | `app/agent/orchestrator.py`、`app/agent/planner.py` | `tests/test_step_contracts.py`、`tests/test_continuous_tasks.py` |
| P3.6-T04 | Experience Governance Guardrails | 为 experience 建立隔离、去重、晋升门禁 | DONE | `app/evolution/experience_store.py` | `tests/test_experience_governance.py` |
| P3.6-T05 | Failure Recovery Taxonomy & Suggestion Engine | 归类失败并输出受控恢复建议 | DONE | `app/agent/recovery.py` | `tests/test_recovery_engine.py` |
| P3.6-T06 | Replayable Safety Regression & Red-Team Harness | 建立可重放的安全回归与红队验证能力 | DONE | `app/evolution/regression.py`、`benchmarks/*.json` | `tests/test_replayable_regression.py` |
| P3.6-T07 | Operator Control Panel UX I | 第一阶段控制面展示解释卡、证据、确认绑定与恢复建议 | DONE | `app/api/chat.py` 的 `operator_panel` 投影、`app/ui/` | `tests/test_operator_panel_core.py` |
| P3.6-T08 | Operator Control Panel UX II | 第二阶段控制面展示 replay、blast radius 与 policy simulator | DONE | `app/agent/previews.py`、`app/ui/app.js` | `tests/test_operator_panel_preview.py` |

---

## 4. 验证目标

Phase 3.6 需要证明的每一条，都有一个具体断言：

| 要证明的事 | 断言它的测试 |
|---|---|
| explanation card 结构完整，且 evidence 引用可追溯 | `tests/test_evidence_layer.py::test_explanation_card_key_sections_are_backed_by_valid_evidence_refs` |
| confirmation token 绑定执行闭包，不能跨范围复用 | `tests/test_confirmation_token.py::test_correct_confirmation_with_plan_hash_mismatch_does_not_execute`、`::test_host_change_invalidates_confirmation_token`、`::test_target_change_invalidates_confirmation_token`、`::test_policy_version_change_invalidates_confirmation_token`、`::test_expired_confirmation_token_does_not_execute` |
| 连续任务在 drift 后会重新校验，而不是沿用过期上下文 | `tests/test_step_contracts.py::test_host_drift_invalidates_resume_and_emits_drift_timeline_event`、`::test_current_user_and_sudo_drift_refuse_resume_before_write` |
| 重校验本身不可用时 fail-closed | `tests/test_step_contracts.py::test_resume_fails_closed_when_revalidation_env_probe_is_unavailable` |
| checkpoint resume 保留 step contract、审计链和风险上下文 | `tests/test_continuous_tasks.py::test_confirmation_mismatch_keeps_pending_and_resume_does_not_rerun_prior_steps` |
| experience 不能靠一次成功就晋升 | `tests/test_experience_governance.py::test_single_success_does_not_auto_promote` |
| failure recovery suggestion 是受控建议，不是脚本生成器 | `tests/test_recovery_engine.py::test_recovery_suggestions_never_cross_boundaries` |
| replayable regression 能稳定重放关键安全路径 | `tests/test_replayable_regression.py::test_replayable_regression_cases_pass`、`::test_run_suite_returns_stable_summary` |
| 坏的 evidence 引用必须 fail-closed，而不是静默通过 | `tests/test_replayable_regression.py::test_bad_evidence_refs_fail_closed` |
| 控制面 UX 展示有真实证据支撑，不是表层叙述 | `tests/test_operator_panel_core.py::test_api_chat_returns_explanation_card_and_operator_panel_projection` |
| 前端不参与 allow/deny，页面上没有 raw shell 输入框 | `tests/test_operator_panel_preview.py::test_frontend_does_not_participate_in_allow_or_deny_decisions`、`tests/test_operator_panel_core.py::test_page_has_no_raw_shell_input_and_keeps_natural_language_entry` |
| blast radius 与 policy simulator 建立在真实 policy trace 上 | `tests/test_operator_panel_preview.py::test_dangerous_refusal_exposes_policy_simulator_details`、`::test_delete_user_request_exposes_blast_radius_preview` |

---

## 5. 与 P4/P5 的阶段关系

P4/P5 当前继续暂缓。

原因不是取消，而是当前更高 ROI 的工作仍在 Phase 3.6：

- 先补齐可信控制面、证据层和鲁棒闭环，后续 P4 的审计与演示材料才更有说服力；
- 先补齐 replayable regression 与失败恢复路径，后续 P5 的最终交付和答辩材料才更稳定；
- 若现在直接进入 P4/P5，会把解释链、确认绑定和回归能力的短板带入最终展示。

因此，Phase 3.6 是当前通向 P4/P5 的前置强化阶段，而不是新的能力扩张阶段。
