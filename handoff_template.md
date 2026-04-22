# GuardedOps Handoff Template

## 1. ChatGPT → Codex 交接模板

```text
项目：GuardedOps
交接方向：ChatGPT → Codex
当前 Phase：
当前 Task ID：
任务名称：

任务目标：
本轮只允许完成：

禁止越界：
- 不允许实现 raw shell / arbitrary shell
- 不允许修改未列出的文件
- 不允许扩大功能范围
- 不允许改变架构边界
- 不允许绕过 policy engine
- 不允许写未要求的业务能力

必须读取的上下文文件：
- agent.md
- project_context.md
- architecture_constraints.md
- current_status.md
- task_board.md

允许修改/新增文件：
-

禁止修改文件：
-

当前已完成内容：
-

当前未完成内容：
-

需要遵守的关键约束：
-

完成标准：
-

测试/验证要求：
-

输出要求：
1. 列出修改文件；
2. 列出新增文件；
3. 列出测试结果；
4. 列出未完成项；
5. 不要做额外功能。
```

---

## 2. Codex → ChatGPT 交接模板

```text
项目：GuardedOps
交接方向：Codex → ChatGPT
当前 Phase：
当前 Task ID：
任务名称：

本轮完成内容：
-

修改文件：
-

新增文件：
-

删除文件：
-

测试结果：
-

未完成内容：
-

遇到的问题：
-

可能影响的后续任务：
-

是否偏离原任务：
- 是/否
- 如是，说明原因：

需要 ChatGPT 判断的问题：
-
```

---

## 3. ChatGPT → Gemini 交接模板

```text
项目：GuardedOps
交接方向：ChatGPT → Gemini
审查类型：
- 安全审查 / 架构审查 / 文档审查 / 演示审查

当前 Phase：
当前能力范围：
-

已实现能力：
-

明确不做的能力：
-

请重点审查：
1. 是否存在 arbitrary shell 风险；
2. 是否存在 Prompt 承担最终风控的问题；
3. 是否存在路径绕过；
4. 是否存在用户名注入；
5. 是否存在未经确认的写操作；
6. 是否存在文档夸大实现能力；
7. 是否存在演示场景无法真实验证的问题。

请输出：
- 高风险问题；
- 中风险问题；
- 低风险问题；
- 建议修复顺序；
- 不建议现在做的事项。
```

---

## 4. Gemini → ChatGPT / Codex 交接模板

```text
项目：GuardedOps
交接方向：Gemini → ChatGPT/Codex
审查结论：

高风险问题：
-

中风险问题：
-

低风险问题：
-

建议立即修复：
-

建议暂缓：
-

需要更新的文件：
-

需要新增的测试：
-

可能影响的任务：
-
```

---

## 5. 新线程快速启动摘要模板

```text
你现在接手 GuardedOps 项目。

当前 Phase：
当前 Task ID：
当前目标：
已完成：
未完成：
关键边界：
- Web 主入口，CLI 辅助
- 禁止万能 Shell
- 禁止 raw command mode
- 风控必须代码实现
- 执行层只允许白名单工具
- 写操作必须确认和审计

请先读取：
- agent.md
- project_context.md
- architecture_constraints.md
- current_status.md
- task_board.md
然后只处理当前 Task，不要扩展 scope。
```
