# GuardedOps Architecture Constraints

## 1. 总原则

GuardedOps 必须是一个真实可运行的安全运维代理。

系统必须坚持：

- 结构化意图优先；
- 风控代码优先；
- 工具白名单优先；
- 默认拒绝未知写操作；
- 所有敏感操作必须确认；
- 所有执行必须审计；
- 所有演示必须可复现。

Prompt 不能作为最终安全边界。

---

## 2. 必须实现的系统层级

### 2.1 交互层

必须至少包含：

- Web Chat Console；
- CLI Debug Entry。

Web 是主演示入口，CLI 是调试入口。

### 2.2 Agent 层

必须至少包含：

- parser；
- planner；
- orchestrator；
- session memory；
- summarizer。

### 2.3 风控层

必须至少包含：

- risk engine；
- validators；
- protected path rules；
- confirmation policy；
- scope limiter。

### 2.4 执行层

必须至少包含：

- BaseExecutor；
- LocalExecutor；
- SSHExecutor；
- tool wrappers。

### 2.5 审计层

必须至少包含：

- SQLite 审计；
- JSONL 审计；
- 可查看最近操作记录。

---

## 3. 必须做

MVP 必须完成：

- 环境探测；
- 磁盘使用情况查询；
- 文件或目录检索；
- 进程查询；
- 端口查询；
- 普通用户创建；
- 普通用户删除；
- 高风险请求拒绝；
- 二次确认；
- 多轮上下文；
- 连续任务闭环；
- 审计日志；
- 提交文档三件套：
  - Agent 配置说明；
  - 核心 Prompt 文本；
  - 工具及能力定义文档。

---

## 4. 暂不做

以下内容暂不做：

- 语音；
- 图像；
- 多模态；
- Kubernetes；
- Docker；
- 云主机自动发现；
- 自动修复系统配置；
- 防火墙规则修改；
- 服务启停；
- crontab 修改；
- 软件包安装卸载；
- LLM 自主生成 bash；
- 多 Agent 框架；
- 长期后台守护进程。

这些功能不得由 Codex 自行添加。

---

## 5. 严禁做

以下内容严禁实现：

- arbitrary shell execution；
- raw command mode；
- “输入任意命令并执行”功能；
- `shell=True` 执行用户可控字符串；
- 用户自然语言直接拼接为 shell；
- 删除 `/`、`/etc`、`/boot`、`/usr`、`/bin`、`/sbin`、`/lib`、`/lib64`；
- 修改 `/etc/sudoers`；
- 修改 `/etc/ssh/sshd_config`；
- 批量 chmod/chown；
- 批量删除用户；
- 创建 root/sudo/admin 用户；
- 给普通用户添加 sudo 权限；
- 删除 root 用户；
- 删除当前登录用户；
- 删除 UID < 1000 的系统用户；
- 不经确认执行写操作；
- 不经验证返回写操作成功。

---

## 6. 工具白名单

执行层只允许以下工具：

| 工具名 | 类型 | 风险等级 | 说明 |
|---|---|---|---|
| env_probe_tool | 只读 | S0 | 探测系统环境 |
| disk_usage_tool | 只读 | S0 | 查询磁盘 |
| file_search_tool | 只读 | S0/S1 | 搜索文件，必须限制范围 |
| process_query_tool | 只读 | S0 | 查询进程 |
| port_query_tool | 只读 | S0 | 查询端口 |
| create_user_tool | 写操作 | S1 | 创建普通用户，需确认 |
| delete_user_tool | 写操作 | S2 | 删除普通用户，需强确认 |
| audit_query_tool | 只读 | S0 | 查询审计日志 |

不允许添加 `run_shell_tool`、`execute_command_tool`、`bash_tool` 等通用执行工具。

---

## 7. 风险等级

| 等级 | 含义 | 处理方式 |
|---|---|---|
| S0 | 只读安全 | 可直接执行，但仍需记录审计 |
| S1 | 受限变更 | 需要确认 |
| S2 | 敏感变更 | 需要强确认和更严格验证 |
| S3 | 禁止执行 | 拒绝执行，给出原因和安全替代方案 |

---

## 8. 路径保护规则

### 8.1 受保护路径

以下路径为受保护路径：

```text
/
/etc
/boot
/bin
/sbin
/usr
/lib
/lib64
/dev
/proc
/sys
```

说明：

- 对这些路径的写操作必须拒绝；
- 对 `/dev`、`/proc`、`/sys` 的深度搜索默认拒绝；
- 对 `/` 的全盘搜索默认拒绝，除非显式转换为受限、截断的只读搜索计划；
- 对 `/etc` 的只读列举可允许，但必须限定范围和返回数量。

### 8.2 文件检索限制

文件检索必须满足：

- 必须有 base_path；
- 必须有 max_results；
- 必须有 max_depth；
- 默认 max_results 不超过 20；
- 系统硬上限 max_results 不超过 50；
- 默认 max_depth 不超过 4；
- 禁止无限递归；
- 结果必须截断；
- 输出必须说明是否截断。

---

## 9. 用户名校验规则

普通用户名必须匹配：

```text
^[a-z_][a-z0-9_-]{2,31}$
```

禁止用户名：

```text
root
admin
administrator
sudo
wheel
daemon
bin
sys
sync
games
man
lp
mail
news
uucp
proxy
www-data
backup
list
irc
gnats
nobody
systemd-network
systemd-resolve
sshd
```

禁止：

- 空用户名；
- 中文用户名；
- 包含空格；
- 包含分号；
- 包含斜杠；
- 包含反引号；
- 包含 `$()`；
- 包含通配符；
- 批量用户名；
- 多个用户名一次处理。

---

## 10. 普通用户创建约束

创建用户必须满足：

- 只能创建普通用户；
- 不允许加入 sudo/wheel/admin 组；
- 必须先检查用户是否已存在；
- 必须要求确认；
- 必须执行后验证；
- 必须记录审计；
- 必须报告是否创建 home 目录；
- 如果权限不足，必须明确提示，不允许尝试绕过。

---

## 11. 普通用户删除约束

删除用户必须满足：

- 只能删除普通用户；
- 必须先检查用户存在；
- 必须检查 UID；
- UID < 1000 默认视为系统用户，禁止删除；
- 不允许删除当前登录用户；
- 默认不删除 home 目录；
- 必须强确认；
- 必须执行后验证；
- 必须记录审计。

强确认语格式：

```text
确认删除普通用户 <username>
```

---

## 12. 执行器约束

执行器必须满足：

- 使用 argv list；
- 不使用 `shell=True`；
- 不拼接用户原始输入；
- 每个命令必须有 timeout；
- stdout/stderr 必须截断；
- exit_code 必须记录；
- 命令失败不得伪装成功；
- SSH 和 Local 必须使用统一 CommandResult 结构。

---

## 13. Web UI 约束

Web UI 必须展示：

- 用户原始输入；
- 识别意图；
- 风险等级；
- 风险原因；
- 执行计划；
- 是否需要确认；
- 执行状态；
- 执行结果；
- 审计记录入口。

Web UI 不需要花哨动画。

---

## 14. CLI 约束

CLI 用于：

- 调试；
- smoke test；
- 快速验证工具；
- 本地开发。

CLI 不得提供 raw shell mode。

---

## 15. 审计约束

每次请求必须记录：

- request_id；
- timestamp；
- raw_user_input；
- parsed_intent；
- environment_snapshot；
- risk_decision；
- confirmation_status；
- tool_calls；
- command_results；
- final_status；
- final_answer。

审计日志不得只记录最终自然语言答案。

---

## 16. LLM 约束

如果后续启用 LLM：

- LLM 输出必须是结构化 JSON；
- LLM 输出必须经过 validators；
- LLM 不得直接输出可执行命令；
- LLM 不得绕过 policy engine；
- LLM 不得决定最终 allow / deny；
- LLM 可用于解释、摘要和意图候选。

---

## 17. 待确认项

- 是否统一使用 sudo wrapper；
- SSH 密钥路径配置格式；
- 是否启用真实 LLM；
- 最终演示环境发行版；
- 测试用户名前缀；
- 是否允许在演示机器上真实 useradd/userdel。
