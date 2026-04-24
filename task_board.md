# GuardedOps Task Board

## 状态说明

| 状态 | 含义 |
|---|---|
| NOT_STARTED | 未开始 |
| IN_PROGRESS | 正在进行 |
| BLOCKED | 阻塞 |
| REVIEW_NEEDED | 需要审查 |
| DONE | 已完成 |
| DEFERRED | 暂缓 |

## 并行标记说明

| 标记 | 含义 |
|---|---|
| SERIAL | 必须串行 |
| PARALLEL | 可独立并行 |
| CONDITIONAL | 可条件并行 |

---

## 任务总表

| Task ID | Phase | 任务名 | 目标 | 前置依赖 | 并行标记 | 并行组 | 状态 | 完成标准 |
|---|---|---|---|---|---|---|---|---|
| P0-T01 | Phase 0 | 初始化总控文件 | 创建管理文件体系 | 无 | SERIAL | G0 | DONE | 所有管理文件落盘 |
| P0-T02 | Phase 0 | 初始化仓库骨架 | 创建基础目录、README、依赖文件 | P0-T01 | SERIAL | G0 | DONE | repo 可安装、结构清晰 |
| P0-T03 | Phase 0 | 建立任务执行规范 | 建立 Codex 执行和状态更新规范 | P0-T01 | PARALLEL | G0-DOC | DEFERRED | prompt 模板与状态更新规则可用；Phase 3.6 期间暂缓或低优先级 |
| P1-T01 | Phase 1 | 核心数据模型 | 定义 Intent、PolicyDecision、CommandResult 等模型 | P0-T02 | SERIAL | G1-CORE | DONE | 模型可被导入，测试通过 |
| P1-T02 | Phase 1 | Executor 底座 | 实现 BaseExecutor、LocalExecutor、SSHExecutor 骨架 | P1-T01 | SERIAL | G1-CORE | DONE | 本地 whoami/hostname 可执行 |
| P1-T03 | Phase 1 | 环境探测工具 | 实现 env_probe_tool | P1-T02 | CONDITIONAL | G1-TOOLS | DONE | 能返回系统环境快照 |
| P1-T04 | Phase 1 | 只读工具实现 | 实现 disk/file/process/port 查询工具 | P1-T02 | CONDITIONAL | G1-TOOLS | DONE | 四类只读工具可运行 |
| P1-T05 | Phase 1 | 只读意图解析与编排 | 支持只读自然语言解析、计划和执行 | P1-T03,P1-T04 | SERIAL | G1-ORCH | DONE | 四个只读 demo 可闭环 |
| P1-T06 | Phase 1 | CLI 调试入口 | 提供 CLI 调用只读能力 | P1-T05 | CONDITIONAL | G1-UI | DONE | CLI 可跑只读 demo |
| P1-T07 | Phase 1 | Web/API 只读入口 | 提供 FastAPI 和简单 Web 页面 | P1-T05 | CONDITIONAL | G1-UI | DONE | Web 可展示只读结果 |
| P1-T08 | Phase 1 | Phase 1 测试 | 为执行器和只读工具补测试 | P1-T03,P1-T04,P1-T05 | CONDITIONAL | G1-QA | DONE | pytest 通过 |
| P2-T01 | Phase 2 | 风控引擎 | 实现风险分级、路径保护、用户名校验 | P1-T01 | SERIAL | G2-POLICY | DONE | 核心风险测试通过 |
| P2-T02 | Phase 2 | 用户管理工具 | 实现普通用户创建/删除工具 | P2-T01,P1-T02 | SERIAL | G2-WRITE | DONE | create/delete 工具具备验证流程 |
| P2-T03 | Phase 2 | 确认状态机 | 实现确认语和 pending action 机制 | P2-T01 | CONDITIONAL | G2-CONFIRM | DONE | S1/S2 操作需确认 |
| P2-T04 | Phase 2 | 高风险拒绝闭环 | 实现危险请求识别、拒绝与替代建议 | P2-T01 | CONDITIONAL | G2-POLICY | DONE | /etc 删除等请求被拒绝 |
| P2-T05 | Phase 2 | Web 风险与确认界面 | Web 展示风险、计划、确认按钮/确认语 | P2-T03,P2-T04,P1-T07 | CONDITIONAL | G2-UI | DONE | Web 可完成创建/删除确认 |
| P2-T06 | Phase 2 | Phase 2 测试 | 补充用户管理、确认、高风险拒绝测试 | P2-T02,P2-T03,P2-T04 | CONDITIONAL | G2-QA | DONE | 风控测试通过 |
| P3-T01 | Phase 3 | Session Memory | 实现 last_username/path/port/pid 等上下文 | P2-T03 | SERIAL | G3-MEMORY | DONE | 可解析“刚才那个用户” |
| P3-T02 | Phase 3 | 多步 Planner | 实现连续任务计划拆解 | P3-T01,P2-T02 | SERIAL | G3-PLAN | DONE | 可生成多步计划 |
| P3-T03 | Phase 3 | 连续任务 Orchestrator | 实现逐步执行、失败中止、执行后验证 | P3-T02 | SERIAL | G3-ORCH | DONE | 创建-验证-删除闭环可跑 |
| P3-T04 | Phase 3 | LLM Parser Stub 与 Prompt 文档 | 预留 LLM JSON 接口并记录 Prompt | P1-T05 | PARALLEL | G3-DOC | DONE | 不启用真实 LLM 也不影响运行 |
| P3-T05 | Phase 3 | Phase 3 测试 | 测试上下文、多轮、连续任务 | P3-T03 | CONDITIONAL | G3-QA | DONE | 多轮 demo 测试通过 |
| P3.5-T00 | Phase 3.5 | 更新总控文件以加入 Evo-Lite 阶段 | 在总控体系中记录 Evo-Lite 阶段、边界和任务编号 | Phase 3 已完成 | SERIAL | G3.5-CONTROL | DONE | task_board、current_status、architecture_constraints、decision_log、parallel_workstreams 已记录 Phase 3.5；docs/evo_lite_design.md 已新增 |
| P3.5-T01 | Phase 3.5 | Execution Evaluator | 将真实执行结果转化为受控评估记录 | P3.5-T00 | PARALLEL | G3.5-EVAL | DONE | 可基于审计/执行结果产出结构化评估，不触发执行能力扩展 |
| P3.5-T02 | Phase 3.5 | Experience Store | 存储可审计、可检索的安全经验记录 | P3.5-T00 | PARALLEL | G3.5-STORE | DONE | 经验记录可追溯来源，不修改 policy、executor 或模型权重 |
| P3.5-T03 | Phase 3.5 | Reflection Generator | 基于评估结果生成安全反思与经验条目 | P3.5-T01 | CONDITIONAL | G3.5-REFLECT | DONE | reflection 只写入经验，不改变系统边界或执行规则 |
| P3.5-T04 | Phase 3.5 | Safe Workflow Templates | 沉淀只调用白名单工具的安全工作流模板 | P3.5-T00 | PARALLEL | G3.5-WORKFLOW | DONE | workflow 模板只包含受控步骤和白名单工具，不生成可执行脚本 |
| P3.5-T05 | Phase 3.5 | Workflow Retrieval in Planner | 允许 planner 检索安全 workflow 作为建议 | P3.5-T04 | CONDITIONAL | G3.5-PLAN | DONE | planner 可读取 workflow 建议，但最终仍必须经过 policy 与工具白名单 |
| P3.5-T06 | Phase 3.5 | Evo-Lite Orchestrator Hook | 在 orchestrator 边界内挂接评估、经验和 workflow 建议 | P3.5-T01,P3.5-T02,P3.5-T03,P3.5-T05 | SERIAL | G3.5-ORCH | DONE | hook 不绕过确认、policy、executor 或审计流程 |
| P3.5-T07 | Phase 3.5 | Safety Regression Benchmark | 建立 Evo-Lite 安全回归基准 | P3.5-T06 | SERIAL | G3.5-QA | DONE | 覆盖禁止训练、禁止 raw shell、禁止绕过 policy 的回归用例 |
| P3.6-T00 | Phase 3.6 | 更新总控文件并加入 Phase 3.6 | 在总控体系中正式插入 Phase 3.6 及其任务链 | Phase 3.5 已完成 | SERIAL | G3.6-CONTROL | DONE | task_board、current_status、architecture_constraints、parallel_workstreams、decision_log、validation_matrix 已写入 Phase 3.6；docs/phase_3_6_design.md 可新增 |
| P3.6-T01 | Phase 3.6 | Evidence Layer Schema & Explanation Card Backend | 建立解释卡与证据层统一 schema，并约束证据来源 | P3.6-T00 | SERIAL | G3.6-EVIDENCE | NOT_STARTED | 解释卡与证据层有统一 schema，证据优先来自 trace / state assertion / policy events |
| P3.6-T02 | Phase 3.6 | Guarded Confirmation Token & Scope Binding | 让确认令牌与执行闭包、作用域和风险等级绑定 | P3.6-T01 | PARALLEL | G3.6-CONFIRM | NOT_STARTED | confirmation token 与执行闭包、作用域、风险等级绑定，不能脱离闭包复用 |
| P3.6-T03 | Phase 3.6 | Step Contracts, Drift Revalidation & Checkpoint Resume | 为连续任务建立 step contract、漂移重校验与断点续跑约束 | P3.6-T01,P3.6-T02 | SERIAL | G3.6-RESUME | NOT_STARTED | 多步任务具备 step contract、漂移重校验、断点续跑与检查点恢复约束 |
| P3.6-T04 | Phase 3.6 | Experience Governance Guardrails | 为 experience 建立隔离、去重和晋升门禁 | P3.6-T01 | PARALLEL | G3.6-GOVERN | NOT_STARTED | experience 具备隔离、去重、晋升门禁，不能直接成为 allow/deny 来源 |
| P3.6-T05 | Phase 3.6 | Failure Recovery Taxonomy & Suggestion Engine | 将失败归类并输出受控恢复建议 | P3.6-T03,P3.6-T04 | SERIAL | G3.6-RECOVERY | NOT_STARTED | 失败被归类并给出受控恢复建议，不生成可执行 shell 脚本 |
| P3.6-T06 | Phase 3.6 | Replayable Safety Regression & Red-Team Harness | 建立可重放的安全回归与红队验证框架 | P3.6-T05 | PARALLEL | G3.6-QA | NOT_STARTED | 安全回归可重放、可复现，覆盖 evidence / confirmation / drift / recovery 关键路径 |
| P3.6-T07 | Phase 3.6 | Operator Control Panel UX I | 建设第一阶段可信控制面展示解释、证据与恢复信息 | P3.6-T05 | PARALLEL | G3.6-UX1 | NOT_STARTED | 控制面第一阶段可展示解释卡、证据来源、确认绑定、恢复建议 |
| P3.6-T08 | Phase 3.6 | Operator Control Panel UX II | 建设第二阶段可信控制面展示 replay、blast radius 与 simulator | P3.6-T06,P3.6-T07 | SERIAL | G3.6-UX2 | NOT_STARTED | 控制面第二阶段补齐 replay、blast radius、policy simulator 等可信展示能力 |
| P4-T01 | Phase 4 | 审计存储 | 实现 SQLite + JSONL 审计 | P1-T05,P2-T03 | SERIAL | G4-AUDIT | DEFERRED | 每次请求有完整审计；Phase 3.6 期间暂缓 |
| P4-T02 | Phase 4 | 审计查询与导出 | 实现审计 API/UI 和导出 | P4-T01,P1-T07 | CONDITIONAL | G4-AUDIT | DEFERRED | 可查看和导出审计；Phase 3.6 期间暂缓 |
| P4-T03 | Phase 4 | Demo 场景脚本 | 编写 6 个演示场景和验证步骤 | P3-T03,P4-T01 | PARALLEL | G4-DEMO | DEFERRED | demo_scenarios 可直接照录；Phase 3.6 期间暂缓 |
| P4-T04 | Phase 4 | 自测验证矩阵 | 映射功能到评分点和验证材料 | P4-T03 | PARALLEL | G4-DOC | DEFERRED | validation_matrix 完整；Phase 3.6 期间暂缓 |
| P4-T05 | Phase 4 | 异常处理与恢复提示 | 增强权限不足、命令缺失等反馈 | P4-T01 | CONDITIONAL | G4-QA | DEFERRED | 失败场景反馈清晰；Phase 3.6 期间暂缓 |
| P5-T01 | Phase 5 | Agent 配置说明 | 完成 agent_config 文档 | P4-T03 | PARALLEL | G5-DOC | DEFERRED | 文档可提交；P4 完成前暂缓 |
| P5-T02 | Phase 5 | 工具能力定义文档 | 完成 tools_and_capabilities 文档 | P4-T03 | PARALLEL | G5-DOC | DEFERRED | 工具范围和限制清晰；P4 完成前暂缓 |
| P5-T03 | Phase 5 | 架构与安全说明 | 完成 architecture/security 文档 | P4-T04 | PARALLEL | G5-DOC | DEFERRED | 可解释设计取舍；P4 完成前暂缓 |
| P5-T04 | Phase 5 | 演示视频脚本与提交清单 | 形成录屏脚本和最终 checklist | P5-T01,P5-T02,P5-T03 | SERIAL | G5-FINAL | DEFERRED | 可按脚本录制；P4/P5 恢复后执行 |
| P5-T05 | Phase 5 | Gemini 安全审查包 | 准备安全审查输入和修复清单 | P5-T04 | CONDITIONAL | G5-REVIEW | DEFERRED | 有审查问题列表；P4/P5 恢复后执行 |
| P5-T06 | Phase 5 | 最终冻结与发布 | 最终清理、版本标记、提交包检查 | P5-T05 | SERIAL | G5-FINAL | DEFERRED | 可提交版本完成；P4/P5 恢复后执行 |

---

## 当前活跃任务

- 当前阶段：Phase 3.6
- 当前任务：P3.6-T00（已完成）
- 当前主线：可信控制面、证据层与鲁棒闭环
- 下一步建议：P3.6-T01
- 当前阻塞：无业务实现阻塞；P4/P5 暂缓；P0-T03 暂缓或低优先级；不扩大系统能力边界
- 最新决策：DEC-P36-01 新增 Phase 3.6，优先建设可信控制面与证据层

---

## 更新记录

| 日期 | 更新人 | 更新内容 |
|---|---|---|
| 2026-04-22 | ChatGPT | 完成 P0-T01 总控管理文件体系初始化 |
| 2026-04-22 | Codex | 完成 P0-T02 仓库骨架初始化；当前目录不是 Git 工作区，未提交或推送 |
| 2026-04-23 | Codex | 完成 P3.5-T00 总控文件更新，加入 Evo-Lite 阶段与任务编号 |
| 2026-04-23 | Codex | 完成 P3.6-T00 总控文件更新，加入 Phase 3.6 阶段、任务链与设计说明 |
