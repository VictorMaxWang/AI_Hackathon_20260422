# Sudo Wrapper Deployment

GuardedOps 的两个写操作（创建普通用户、删除普通用户）不会直接调用 `useradd` / `userdel`，而是固定调用仓库里的两个 wrapper 脚本。这份文档说明这些 wrapper 在哪、需要什么权限、怎么部署，以及目前有哪些没有交付。

## 1. 为什么要有 wrapper

执行层是 argv-only 的（`app/executors/base.py::BaseExecutor._validate_argv`），不使用 `shell=True`，也不拼接用户原始输入。即便如此，写操作仍然走 wrapper，原因是**纵深防御**：

- Python 侧的校验（`app/policy/validators.py`、`app/tools/user.py`）可能被未来的重构绕过；
- wrapper 在目标主机上再独立校验一次同样的规则，用的是与 Python 完全不同的实现；
- wrapper 是唯一被授予 root 的东西，`useradd` / `userdel` 本身不对代理进程开放；
- wrapper 的参数面极小，只接受两个位置参数，任何多余参数直接以退出码 64 拒绝。

换句话说：即使代理进程被完全攻陷，它能对用户体系做的事情也被限制在这两个脚本能表达的范围内。

## 2. wrapper 在哪、怎么被调用

| 脚本 | 用途 | 调用形式 |
|---|---|---|
| `scripts/guardedops_create_user.sh` | 创建普通用户 | `bash <wrapper> (--create-home\|--no-create-home) <username>` |
| `scripts/guardedops_delete_user.sh` | 删除普通用户 | `bash <wrapper> (--keep-home\|--remove-home) <username>` |

调用点在 `app/tools/user.py`，通过 `CREATE_USER_WRAPPER` / `DELETE_USER_WRAPPER` 两个常量拼进 argv。

**路径解析：** 两个常量都从 `WRAPPER_DIR = Path(__file__).resolve().parents[2] / "scripts"` 派生，也就是**相对模块文件锚定到仓库根**，与进程当前工作目录无关。从任何目录启动 uvicorn 或 CLI 都能解析到同一个脚本。

（历史实现是裸相对路径 `scripts/guardedops_create_user.sh`，从仓库根以外启动时写操作会因为找不到脚本而失败，而且报错看起来像环境问题而不是安全拒绝。）

调用前还有一道 `_wrapper_preflight`：路径必须是绝对路径且确实是一个文件，否则直接返回错误，不会去执行 `bash`。

**从 wheel 安装时：** `parents[2]` 会指向 site-packages 的上一层，那里没有 `scripts/`，preflight 会明确报 `wrapper script is missing: ...`。这是预期的失败方式（明确报错而不是静默降级），但也意味着**写操作目前只在从仓库源码运行时可用**。

Evo-Lite 的 workflow 模板曾经有同样的"文件在 `app` 包外、不进 wheel"问题，现在已经修好：模板搬进了 `app/evolution/templates/`，由 `pyproject.toml` 的 `package-data` 打进包，`tests/test_packaging.py` 每次都断言它们在 wheel 里。**wrapper 不能照搬这个修法。** 模板是纯数据，谁能读都无所谓；wrapper 是唯一被授予 root 的可执行文件，第 4 节要求它 root 所有、`0755`、且不能被运行 GuardedOps 的账号写入。site-packages 通常属于安装它的那个账号，把 wrapper 放进去等于把 NOPASSWD 授权交给一个可写路径。

如果你要把 wrapper 装到 `/usr/local/libexec/guardedops/` 之类的系统位置，请通过修改这两个常量或引入一个显式配置项来做，不要依赖 CWD，也不要依赖包安装位置。

## 3. wrapper 自己会拒绝什么

两个脚本都独立重做一遍安全校验，与 Python 侧的规则一致：

- 用户名必须匹配 `^[a-z_][a-z0-9_-]{2,31}$`；
- 用户名不能落在保留名单里（`root`、`admin`、`sudo`、`wheel`、`www-data`、`sshd` 等）；
- 第一个参数必须是允许的 home 模式，其他值一律拒绝；
- 创建前必须确认用户不存在；
- 删除前必须确认用户存在、UID ≥ 1000、且不是当前登录用户。

退出码约定：

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 64 | 参数个数或 home 模式非法 |
| 65 | 用户名非法或落在保留名单 |
| 70 | 前置状态不满足（创建时已存在 / 删除时不存在 / passwd 记录异常） |
| 71 | 拒绝删除系统用户（UID < 1000）或当前登录用户 |

非 0 退出码会被 `app/tools/user.py` 转成 `ToolResult(success=False)`，并且**不会**被当作成功上报：写操作在 wrapper 返回后还会再跑一次 `getent passwd` 做状态断言。

## 4. 需要什么权限

`/usr/sbin/useradd` 和 `/usr/sbin/userdel` 需要 root。因此运行 GuardedOps 的账号需要具备下列之一：

**方案 A（推荐）：为 wrapper 单独授权，不给代理进程通用 sudo。**

由主机管理员手工部署（GuardedOps 自己**永远不会**修改 sudoers，这是 S3 拒绝项）：

```bash
# 以管理员身份执行
sudo install -o root -g root -m 0755 \
  scripts/guardedops_create_user.sh /usr/local/libexec/guardedops/guardedops_create_user.sh
sudo install -o root -g root -m 0755 \
  scripts/guardedops_delete_user.sh /usr/local/libexec/guardedops/guardedops_delete_user.sh
```

然后新增一个只覆盖这两个脚本的 sudoers 片段（用 `visudo -f` 编辑，不要直接写 `/etc/sudoers`）：

```text
guardedops ALL=(root) NOPASSWD: /usr/local/libexec/guardedops/guardedops_create_user.sh, \
                                /usr/local/libexec/guardedops/guardedops_delete_user.sh
```

关键要求：

- 脚本必须 root 所有、`0755`，**不能**对运行 GuardedOps 的账号可写。否则该账号可以改写脚本内容，NOPASSWD 就等价于通用 root。
- 授权粒度必须是这两个具体路径，不能是目录通配、不能是 `ALL`。
- 采用这个方案时，`CREATE_USER_WRAPPER` / `DELETE_USER_WRAPPER` 需要相应改成 `sudo /usr/local/libexec/guardedops/...` 形式的 argv。当前代码用的是 `bash <path>`，不带 `sudo`。

**方案 B（仅限一次性演示机）：直接以 root 运行 GuardedOps。**

简单，但违背最小权限原则，代理进程的任何缺陷都直接是 root 级别的。只在可丢弃的演示虚拟机上这么做。

## 5. SSH 场景

`SSHExecutor` 把 argv 用 `shlex.join` 拼成一条远程命令执行，因此上面的 argv 会原样在**远端**求值。这意味着：

- wrapper 必须**预先部署在远端主机上**，并且路径在远端有效；本地仓库里的脚本不会被上传；
- 远端账号需要满足第 4 节的权限要求；
- 本地那套"把路径锚定到仓库位置"的做法在远端不成立，SSH 模式必须使用绝对路径。

**当前未交付：** 没有任何入口能实际走到 `SSHExecutor`。`app/api/chat.py` 的 `get_executor()` 和 `app/cli.py` 的 `run_request()` 都硬编码 `LocalExecutor`，也没有地方能构造 `SSHConnectionConfig`。因此本节描述的是设计意图，不是可以照做的部署步骤。详见 `docs/process/validation_matrix.md` 第 6 节。

## 6. 明确不在 wrapper 范围内

wrapper 不做、也不会被扩展去做：

- 修改用户组、授予 sudo/wheel/admin；
- 修改 `/etc/sudoers`、`/etc/ssh/sshd_config`；
- 批量处理多个用户名；
- 接受任何形式的自由命令字符串。

任何要求 wrapper 承担上述能力的改动，都属于扩大执行面，需要先更新 `docs/process/architecture_constraints.md` 与 `docs/process/decision_log.md`。
