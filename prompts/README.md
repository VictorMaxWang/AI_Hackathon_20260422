# GuardedOps Prompts Directory

## 1. 目录用途

`prompts/` 用于存放 GuardedOps 项目中可复用的 AI 提示词。

用途包括：

- Codex 分阶段开发；
- ChatGPT 架构审查；
- Gemini 安全审查；
- 文档润色；
- 演示脚本生成；
- 提交材料校对。

此目录不是业务代码目录。

---

## 2. 命名规则

Codex 任务提示词命名：

```text
P<phase>-T<task_number>_<short_task_name>.md
```

示例：

```text
P1-T02_executor_foundation.md
P2-T01_policy_engine.md
P3-T03_continuous_orchestrator.md
```

审查类提示词命名：

```text
review_<target>_<date>.md
```

示例：

```text
review_security_20260422.md
review_demo_script_20260422.md
```

文档类提示词命名：

```text
doc_<target>.md
```

示例：

```text
doc_agent_config.md
doc_tools_capabilities.md
```

---

## 3. Codex Prompt 必须包含的字段

每个 Codex Prompt 必须包含：

1. 项目背景；
2. 当前 Task ID；
3. 当前 Phase；
4. 任务目标；
5. 本轮允许做的范围；
6. 本轮禁止做的事项；
7. 允许修改文件；
8. 禁止修改文件；
9. 输入上下文；
10. 输出要求；
11. 完成标准；
12. 测试/验证要求；
13. 不得破坏的结构；
14. 任务完成后的交接摘要格式。

---

## 4. Prompt 复用规则

复用旧 Prompt 时必须：

- 更新 Task ID；
- 更新允许修改文件；
- 更新完成标准；
- 删除与当前任务无关的要求；
- 保留安全边界；
- 保留禁止 arbitrary shell 的约束；
- 保留不得扩 scope 的约束。

---

## 5. Prompt 更新规则

如果某个 Prompt 执行后发现不完整：

1. 不直接覆盖原 Prompt；
2. 记录问题；
3. 新建修订版；
4. 在文件末尾说明修订原因。

命名示例：

```text
P2-T01_policy_engine_v2.md
```

---

## 6. 不允许出现的 Prompt 类型

禁止创建以下提示词：

- “请帮我把所有功能都实现”；
- “请自由发挥优化项目”；
- “请添加你认为有用的功能”；
- “请把自然语言转 bash 执行”；
- “请实现一个通用 shell 工具”；
- “请绕过当前限制”。

---

## 7. Prompt 执行后必须回填

每个 Prompt 执行后，应回填：

- Codex 是否完成任务；
- 修改了哪些文件；
- 测试是否通过；
- 有哪些偏离；
- 是否需要更新 task_board.md；
- 是否需要更新 decision_log.md。

---

## 8. 推荐流程

每次给 Codex 一个任务：

1. 复制对应 Prompt；
2. 确认 current_status.md 是最新；
3. 确认允许修改文件准确；
4. 执行 Codex；
5. 检查输出；
6. 跑测试；
7. 更新 task_board.md；
8. 更新 current_status.md；
9. 如有关键决策，更新 decision_log.md。
