# Codex Prompt Template for GuardedOps

请使用以下模板创建每个 Codex 任务提示词。

---

## Prompt 模板

```text
你现在是 GuardedOps 项目的 Codex 工程执行者。

项目背景：
GuardedOps 是 AI Hackathon 2026《操作系统智能代理》的项目，目标是构建一个安全对话式 Linux/SSH 运维代理。系统必须真实可运行、可验证、可演示、可审计。它不是万能 shell 聊天机器人，不允许 arbitrary shell，不允许 raw command mode，不允许把用户自然语言直接拼成 bash 执行。

当前 Phase：
<填写 Phase>

当前 Task ID：
<填写 Task ID>

任务名称：
<填写任务名>

任务目标：
<填写本轮目标>

本轮只允许做：
<列出明确范围>

本轮禁止做：
- 不允许实现 arbitrary shell / raw command mode
- 不允许新增未要求的业务能力
- 不允许改变 architecture_constraints.md 中的边界
- 不允许让 Prompt 承担最终风控边界
- 不允许使用 shell=True 执行用户可控字符串
- 不允许修改未列出的文件
- 不允许删除已有结构
- 不允许扩大任务范围

必须读取的上下文：
- agent.md
- project_context.md
- architecture_constraints.md
- current_status.md
- task_board.md

允许新增/修改的文件：
<列出文件或目录>

禁止修改的文件：
<列出文件或目录>

实现要求：
<详细要求>

输出要求：
1. 列出新增文件；
2. 列出修改文件；
3. 说明关键设计；
4. 说明测试结果；
5. 说明未完成项；
6. 如发现需要改架构，停止并说明，不要自行改。

完成判定标准：
<列出可验证标准>

测试/验证要求：
<列出测试方式>

交接摘要格式：
Task ID:
完成内容:
修改文件:
新增文件:
测试结果:
未完成项:
风险/注意事项:
```

---

## 使用注意

- 每次只给 Codex 一个 Task；
- 如果任务很大，先拆分；
- 如果 Codex 提议扩展功能，先拒绝并回到当前 Task；
- 如果 Codex 修改了安全边界，必须人工审查；
- 如果 Codex 添加了 `shell=True`，必须回滚或修复。
