# GuardedOps Current Status

## 1. 当前阶段

- 当前 Phase：Phase 3.6 已完成
- 当前主线：工程化收口——可安装性、CI、文档与真实实现一致
- 当前 Task ID：无进行中的业务实现任务
- 当前状态：P3.6-T00 ~ P3.6-T08 全部完成并有通过的测试；总控文档已按真实实现回填

---

## 2. 当前正在推进什么

Phase 3.6 的可信控制闭环已经落地：证据层、确认令牌绑定、step contract 与断点续跑、经验治理、失败恢复建议、可重放安全回归、Operator Panel 两阶段展示都有实现代码和通过的测试。

当前推进的不是新能力，而是让仓库本身可被第三方复现：仓库可 `pip install`，CI 在三个 Python 版本上跑全量测试，文档不再声称未实现的能力。

系统边界保持不变：不开放 arbitrary shell，不开放 raw command mode，不自动修改 policy/executor，不做在线 RL 或高风险微调。

---

## 3. 下一步是什么

Phase 3.6 任务链已经跑完，下一步不是继续按 T0x 推进，而是二选一：

1. 从 `validation_matrix.md` 第 6 节“未交付能力”里挑选补齐项（优先级建议：SSH 入口 > 审计层 > audit_query_tool）；
2. 由总控决定是否恢复 P4/P5（审计存储、演示材料、交付文档）。

在任一路径开始前都不得扩大执行面。

---

## 4. 当前阻塞点

| 阻塞项 | 状态 | 解决方式 |
|---|---|---|
| P4/P5 | 暂缓 | 由总控决定何时恢复审计、演示材料和最终交付任务 |
| P0-T03 | 未完成，暂缓或低优先级 | 后续如需要统一 Codex 执行模板，再单独恢复；不标记 DONE |
| Phase 3.6 安全边界 | 已锁定 | 后续任务不得开放 arbitrary shell / raw command mode，不得绕过 policy，不得自动修改 policy/executor |
| SSH 远程入口 | 未交付 | `SSHExecutor` 已实现且有测试，但 `app/api/chat.py` 与 `app/cli.py` 都硬编码 `LocalExecutor`，没有入口能构造 `SSHConnectionConfig`；需要一个独立任务补执行器选择 |
| 审计层 | 未实现 | `app/audit/` 目前是空包，`audit_query_tool` 在白名单里但没有实现；属于 P4-T01/P4-T02 范围 |
| 目标 Linux 发行版 | 待确认 | 建议 Ubuntu 22.04 / 24.04 或 openEuler |
| SSH 演示机器 | 待确认 | 先本地模式跑通，再接 SSH |

已从待确认项移出的条目：

- 是否使用真实 LLM：已确定。DashScope / Qwen3.6-Plus provider 已实现（`app/llm/qwen_provider.py`），默认关闭，只在规则解析器返回 `unknown` 时提供意图候选，最终 allow/deny 仍由 policy engine 决定。
- 仓库是否为 Git 工作区：已确定。仓库已初始化并推送到 `https://github.com/VictorMaxWang/AI_Hackathon_20260422`。
- 是否统一使用 sudo wrapper：已确定。写操作统一经过 `scripts/guardedops_create_user.sh` 与 `scripts/guardedops_delete_user.sh`，部署模型见 `../sudo_wrapper_deployment.md`。

---

## 5. 最新决策

| 日期 | 决策 | 原因 | 影响 |
|---|---|---|---|
| 2026-04-22 | Web 为主演示入口，CLI 为辅助入口 | Web 更适合展示风险、计划、确认和审计；CLI 便于调试 | 双入口共用核心引擎 |
| 2026-04-22 | 禁止 arbitrary shell | 降低安全风险，贴合风控评分点 | 只能调用白名单工具 |
| 2026-04-22 | Prompt 不作为最终风控边界 | Prompt 不稳定，安全必须代码化 | 必须实现 policy engine |
| 2026-04-22 | Phase 1 不依赖真实 LLM | 降低试错和环境依赖 | 先用规则 parser |
| 2026-04-23 | DEC-P35-01：新增 Evo-Lite 阶段，采用不改权重的经验沉淀路线 | 让系统从真实执行中沉淀可审计经验，同时不引入训练和自修改风险 | 新增 Phase 3.5 任务链；P4/P5 暂缓 |
| 2026-04-23 | DEC-P36-01：新增 Phase 3.6，优先建设可信控制面与证据层 | 当前 ROI 更高的是提升可信解释、确认绑定、鲁棒闭环与可重放回归，而不是继续扩大能力面 | 新增 Phase 3.6 任务链；P4/P5 继续暂缓；执行面边界保持不变 |

---

## 6. 最近完成内容

| Task ID | 完成内容 | 输出物 | 验收证据 |
|---|---|---|---|
| P0-T01 | 初始化总控管理文件体系 | 11 个管理文件（现位于 `docs/process/`） | 目录存在 |
| P0-T02 | 初始化仓库骨架 | 基础目录、README、pyproject.toml、.gitignore | `pip install -e ".[test]"` 成功；打包发现缺失曾导致长期不可安装，已在工程化收口中修复 |
| P3.5-T00 | 更新总控文件以加入 Evo-Lite 阶段 | 总控文件 + `../evo_lite_design.md` | 文档存在 |
| P3.6-T00 | 更新总控文件并加入 Phase 3.6 | 总控文件 + `../phase_3_6_design.md` | 文档存在 |
| P3.6-T01 | Evidence Layer Schema & Explanation Card Backend | `app/models/evidence.py` | `tests/test_evidence_layer.py` 全绿 |
| P3.6-T02 | Guarded Confirmation Token & Scope Binding | `app/agent/confirmation.py` | `tests/test_confirmation_token.py` 全绿 |
| P3.6-T03 | Step Contracts, Drift Revalidation & Checkpoint Resume | 编排层 step contract 与 checkpoint | `tests/test_step_contracts.py` + `tests/test_continuous_tasks.py` 全绿 |
| P3.6-T04 | Experience Governance Guardrails | `app/evolution/experience_store.py` | `tests/test_experience_governance.py` 全绿 |
| P3.6-T05 | Failure Recovery Taxonomy & Suggestion Engine | `app/agent/recovery.py` | `tests/test_recovery_engine.py` 全绿 |
| P3.6-T06 | Replayable Safety Regression & Red-Team Harness | `benchmarks/safety_regression_v2.json`、`benchmarks/redteam_mutations.json` | `tests/test_replayable_regression.py` 全绿 |
| P3.6-T07 | Operator Control Panel UX I | `app/api/chat.py` 的 operator_panel 投影、`app/ui/` | `tests/test_operator_panel_core.py` 全绿 |
| P3.6-T08 | Operator Control Panel UX II | blast radius / policy simulator 投影 | `tests/test_operator_panel_preview.py` 全绿 |

---

## 7. 当前待办

| 优先级 | Task ID | 任务 | 备注 |
|---|---|---|---|
| High | 待编号 | SSH 执行器入口 | `SSHExecutor` 已实现但无入口可达；需要在 API/CLI 层提供受控的执行器选择 |
| Medium | P4-T01 | 审计存储（SQLite + JSONL） | `app/audit/` 目前为空包，文档已明确标注未实现 |
| Medium | P4-T02 | 审计查询与导出 | 依赖 P4-T01；`audit_query_tool` 目前只在白名单里，没有实现 |
| Low | P0-T03 | 建立任务执行规范 | 暂缓或低优先级，不标记 DONE |
| Deferred | P4-T03/T04/T05、P5 | 演示材料、交付文档、最终冻结 | 由总控决定何时恢复 |

---

## 8. 风险提醒

Phase 3.6 最容易做歪的方向（后续任何改动都要复查）：

- 把 explanation / memory / workflow / reflection 做成绕过 policy 的旁路；
- 把 confirmation token 做成可脱离执行闭包复用的通用授权；
- 把 evidence 写成自由叙述，而不是基于 trace / state assertion / policy events；
- 把 failure recovery suggestion 做成脚本生成器或隐式 raw command mode；
- 让 experience 直接成为 allow / deny 决策来源，绕过隔离、去重和晋升门禁；
- 把 replay、blast radius、policy simulator 做成表层展示，却没有可重放与可审计基础。

文档侧的风险：

- 让 README 或总控文档声称未实现的能力（SSH 入口、审计层、audit_query_tool）已经交付。

---

## 9. 下一次接力摘要模板

```text
项目：GuardedOps
当前 Phase：Phase 3.6 已完成
当前 Task：无进行中的业务实现任务
已完成：P3.6-T00 ~ P3.6-T08 全部落地并有通过的测试；仓库可安装、CI 已建立、文档与实现对齐
正在做：工程化收口后的验收
下一步：从 validation_matrix 第 6 节的未交付能力中选题，或由总控决定是否恢复 P4/P5
当前阻塞：SSH 入口未接通；审计层未实现；P4/P5 暂缓；P0-T03 暂缓或低优先级
关键约束：
- 不开放 arbitrary shell
- 不开放 raw command mode
- explanation / memory / workflow / reflection 不能绕过 policy
- confirmation 必须绑定执行闭包
- evidence 必须优先来自 trace / state assertion / policy events
- workflow 只能调用白名单工具
- experience 必须经过隔离、去重、晋升门禁
- 不自动修改 policy / executor / 风控边界
- 不自动生成可执行 shell 脚本
- benchmark 必须支持回归和重放
- 文档不得声称未实现的能力
需要新线程重点关注：不要扩大系统能力边界；任何新增能力必须同时更新 validation_matrix 和测试
```
