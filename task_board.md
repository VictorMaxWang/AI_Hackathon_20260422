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
| P0-T03 | Phase 0 | 建立任务执行规范 | 建立 Codex 执行和状态更新规范 | P0-T01 | PARALLEL | G0-DOC | NOT_STARTED | prompt 模板与状态更新规则可用 |
| P1-T01 | Phase 1 | 核心数据模型 | 定义 Intent、PolicyDecision、CommandResult 等模型 | P0-T02 | SERIAL | G1-CORE | NOT_STARTED | 模型可被导入，测试通过 |
| P1-T02 | Phase 1 | Executor 底座 | 实现 BaseExecutor、LocalExecutor、SSHExecutor 骨架 | P1-T01 | SERIAL | G1-CORE | NOT_STARTED | 本地 whoami/hostname 可执行 |
| P1-T03 | Phase 1 | 环境探测工具 | 实现 env_probe_tool | P1-T02 | CONDITIONAL | G1-TOOLS | NOT_STARTED | 能返回系统环境快照 |
| P1-T04 | Phase 1 | 只读工具实现 | 实现 disk/file/process/port 查询工具 | P1-T02 | CONDITIONAL | G1-TOOLS | NOT_STARTED | 四类只读工具可运行 |
| P1-T05 | Phase 1 | 只读意图解析与编排 | 支持只读自然语言解析、计划和执行 | P1-T03,P1-T04 | SERIAL | G1-ORCH | NOT_STARTED | 四个只读 demo 可闭环 |
| P1-T06 | Phase 1 | CLI 调试入口 | 提供 CLI 调用只读能力 | P1-T05 | CONDITIONAL | G1-UI | NOT_STARTED | CLI 可跑只读 demo |
| P1-T07 | Phase 1 | Web/API 只读入口 | 提供 FastAPI 和简单 Web 页面 | P1-T05 | CONDITIONAL | G1-UI | NOT_STARTED | Web 可展示只读结果 |
| P1-T08 | Phase 1 | Phase 1 测试 | 为执行器和只读工具补测试 | P1-T03,P1-T04,P1-T05 | CONDITIONAL | G1-QA | NOT_STARTED | pytest 通过 |
| P2-T01 | Phase 2 | 风控引擎 | 实现风险分级、路径保护、用户名校验 | P1-T01 | SERIAL | G2-POLICY | NOT_STARTED | 核心风险测试通过 |
| P2-T02 | Phase 2 | 用户管理工具 | 实现普通用户创建/删除工具 | P2-T01,P1-T02 | SERIAL | G2-WRITE | NOT_STARTED | create/delete 工具具备验证流程 |
| P2-T03 | Phase 2 | 确认状态机 | 实现确认语和 pending action 机制 | P2-T01 | CONDITIONAL | G2-CONFIRM | NOT_STARTED | S1/S2 操作需确认 |
| P2-T04 | Phase 2 | 高风险拒绝闭环 | 实现危险请求识别、拒绝与替代建议 | P2-T01 | CONDITIONAL | G2-POLICY | NOT_STARTED | /etc 删除等请求被拒绝 |
| P2-T05 | Phase 2 | Web 风险与确认界面 | Web 展示风险、计划、确认按钮/确认语 | P2-T03,P2-T04,P1-T07 | CONDITIONAL | G2-UI | NOT_STARTED | Web 可完成创建/删除确认 |
| P2-T06 | Phase 2 | Phase 2 测试 | 补充用户管理、确认、高风险拒绝测试 | P2-T02,P2-T03,P2-T04 | CONDITIONAL | G2-QA | NOT_STARTED | 风控测试通过 |
| P3-T01 | Phase 3 | Session Memory | 实现 last_username/path/port/pid 等上下文 | P2-T03 | SERIAL | G3-MEMORY | NOT_STARTED | 可解析“刚才那个用户” |
| P3-T02 | Phase 3 | 多步 Planner | 实现连续任务计划拆解 | P3-T01,P2-T02 | SERIAL | G3-PLAN | NOT_STARTED | 可生成多步计划 |
| P3-T03 | Phase 3 | 连续任务 Orchestrator | 实现逐步执行、失败中止、执行后验证 | P3-T02 | SERIAL | G3-ORCH | NOT_STARTED | 创建-验证-删除闭环可跑 |
| P3-T04 | Phase 3 | LLM Parser Stub 与 Prompt 文档 | 预留 LLM JSON 接口并记录 Prompt | P1-T05 | PARALLEL | G3-DOC | NOT_STARTED | 不启用真实 LLM 也不影响运行 |
| P3-T05 | Phase 3 | Phase 3 测试 | 测试上下文、多轮、连续任务 | P3-T03 | CONDITIONAL | G3-QA | NOT_STARTED | 多轮 demo 测试通过 |
| P4-T01 | Phase 4 | 审计存储 | 实现 SQLite + JSONL 审计 | P1-T05,P2-T03 | SERIAL | G4-AUDIT | NOT_STARTED | 每次请求有完整审计 |
| P4-T02 | Phase 4 | 审计查询与导出 | 实现审计 API/UI 和导出 | P4-T01,P1-T07 | CONDITIONAL | G4-AUDIT | NOT_STARTED | 可查看和导出审计 |
| P4-T03 | Phase 4 | Demo 场景脚本 | 编写 6 个演示场景和验证步骤 | P3-T03,P4-T01 | PARALLEL | G4-DEMO | NOT_STARTED | demo_scenarios 可直接照录 |
| P4-T04 | Phase 4 | 自测验证矩阵 | 映射功能到评分点和验证材料 | P4-T03 | PARALLEL | G4-DOC | NOT_STARTED | validation_matrix 完整 |
| P4-T05 | Phase 4 | 异常处理与恢复提示 | 增强权限不足、命令缺失等反馈 | P4-T01 | CONDITIONAL | G4-QA | NOT_STARTED | 失败场景反馈清晰 |
| P5-T01 | Phase 5 | Agent 配置说明 | 完成 agent_config 文档 | P4-T03 | PARALLEL | G5-DOC | NOT_STARTED | 文档可提交 |
| P5-T02 | Phase 5 | 工具能力定义文档 | 完成 tools_and_capabilities 文档 | P4-T03 | PARALLEL | G5-DOC | NOT_STARTED | 工具范围和限制清晰 |
| P5-T03 | Phase 5 | 架构与安全说明 | 完成 architecture/security 文档 | P4-T04 | PARALLEL | G5-DOC | NOT_STARTED | 可解释设计取舍 |
| P5-T04 | Phase 5 | 演示视频脚本与提交清单 | 形成录屏脚本和最终 checklist | P5-T01,P5-T02,P5-T03 | SERIAL | G5-FINAL | NOT_STARTED | 可按脚本录制 |
| P5-T05 | Phase 5 | Gemini 安全审查包 | 准备安全审查输入和修复清单 | P5-T04 | CONDITIONAL | G5-REVIEW | NOT_STARTED | 有审查问题列表 |
| P5-T06 | Phase 5 | 最终冻结与发布 | 最终清理、版本标记、提交包检查 | P5-T05 | SERIAL | G5-FINAL | NOT_STARTED | 可提交版本完成 |

---

## 当前活跃任务

- 当前阶段：Phase 0
- 当前任务：P0-T02（已完成）
- 上一任务：P0-T01 已完成
- 当前阻塞：当前目录不是 Git 工作区，尚未提交或推送
- 最新决策：采用 Web 主入口 + CLI 辅助入口；禁止万能 shell；规则风控优先

---

## 更新记录

| 日期 | 更新人 | 更新内容 |
|---|---|---|
| 2026-04-22 | ChatGPT | 完成 P0-T01 总控管理文件体系初始化 |
| 2026-04-22 | Codex | 完成 P0-T02 仓库骨架初始化；当前目录不是 Git 工作区，未提交或推送 |
