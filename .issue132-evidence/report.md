# Issue #132 H1 交付记录（Lord 验收补记）

> 注：Codex 续接会话于 2026-11-21 01:59 UTC 前后终止，未写本报告、未提交。
> 本文件由 Lord 按工作树实物与复跑证据补记，结论以复跑为准，不冒充 Codex 原报告。

## 改了什么（只做 H1）

- `gui/version_update.py`：`begin_update()` 不再同步调 `_flush_embedded_charts()`；
  改走 `window._begin_update_flush()` 回调，图表 ACK 全到后才 `reserve_code_update()`；
  拒绝/超时走原有失败回退，`reserved` 互斥语义不变。失败处理抽为 `_update_preparation_failed()`。
- `gui/installed_update.py`：下载完成处理同样改走回调，ACK 全到后才写 `request.json`；
  拒绝/超时报「图表仍在保存，请稍后再更新。」且不进入半更新状态。
- `gui/main_window.py`：新增 `_begin_update_flush()` / `_end_update_flush()`，
  复用 `_begin_exit_flush` / `_poll_exit_flush` 的 after 状态机门禁；
  关闭与版本更新路径加互斥守卫；`_flush_embedded_charts()` 标注弃用、仅留给旧测试探针。
- 测试：新增 `tests/test_update_flush_nonblocking.py`（6 项）；
  调整 `tests/test_version_update.py`、`tests/test_installed_update_ux.py`、
  `tests/test_legacy_update_bridge.py` 三处旧探针走新回调。

## 不碰

H2（线程池非守护线程/进程滞留）、H3（切股残余阻塞点）、UI 视觉实施包文件、
冻结文件与策略代码均未动。

## 测试证据（先红后绿）

- 先红：`tests/test_update_flush_nonblocking.py` 在修复前 4 failed / 2 passed
  （`.issue132-evidence/red.log`，2026-11-20 16:58 UTC；ack/timeout × source/installed 四项失败）。
- 后绿：修复后 6 passed（`.issue132-evidence/green.log`，19.94s）；
  Lord 2026-11-21 02:5x CST 在工作树复跑：**6 passed（20.84s）**。
- 受影响测试集：`test_version_update.py` + `test_installed_update_ux.py` +
  `test_legacy_update_bridge.py`，Lord 复跑 **122 passed、12 skipped（24.48s）**。
- 冻结摘要不变：`185541a4c226af123a36d113ca741c1b207a5413cd6fb6b4fd980381f0ff312c`
  （修复前后一致，Lord 复跑核实）。

## 未决点

- 新测试用合成 Host 与加速时钟覆盖 ACK 延迟/超时/拒绝三态；
  真实 Windows 桌面上的 15 秒超时与图表交互仍待人工复核（同 #132 主线待决项）。
- 性能：无计时对比；改动只把同步忙等换成 after 轮询，未引入可测回退的路径。

## PR #192 P1 · ACK 后 IPC 清理失败回退

- 更新确认收到 ACK 后，如果 IPC 文件仍被占用，清理会继续尝试其余文件和其余图表 host。每个 host 汇总失败路径后抛出 `OSError`，主窗口写入现有日志。
- 更新从轮询结束或从已关闭的图表直接进入收尾时，清理失败都会执行 `_end_update_flush(False)`，清门禁、恢复图表并把失败交回更新控制器。退出轮询中遇到相同错误则调用 `_abort_exit()` 恢复会话。安装版沿用现有失败回调释放更新预留。
- 性能方面，本改动只碰更新/退出收尾路径，不加任何同步等待。
- 冻结校验前后一致，`verify_frozen()` 返回 `185541a4c226af123a36d113ca741c1b207a5413cd6fb6b4fd980381f0ff312c`。变更限于 `gui/chart_embed.py`、`gui/main_window.py`、`tests/test_update_flush_nonblocking.py` 和本报告。

### 本地代码测试

下列命令使用 `/home/hatch/workspace/Aplus/.venv/bin/python`。

- 修复前运行 `python -m pytest -q tests/test_update_flush_nonblocking.py -k cleanup_failure`，3 项均复现异常外抛。
- 修复后运行 `python -m pytest -q tests/test_update_flush_nonblocking.py -k cleanup`，4 项通过。覆盖两个被占用路径的聚合、其余文件与 host 继续清理、异步 ACK 和已关闭图表两种更新入口的失败回退及重试、退出回退。
- 运行 `python -m pytest -q tests/test_update_flush_nonblocking.py tests/test_version_update.py tests/test_installed_update_ux.py tests/test_legacy_update_bridge.py tests/test_exit_state_machine.py`，134 项通过、12 项跳过、2 项失败。失败用例为 `test_listening_web_port_prevents_code_update` 和 `test_real_closed_loopback_port_is_not_mistaken_for_uncertain_service`，当前沙箱在创建 IPv4 socket 时返回 `PermissionError: [Errno 1]`，用例未执行到业务断言。
- 同一命令追加 `-k 'not listening_web_port_prevents_code_update and not real_closed_loopback_port_is_not_mistaken_for_uncertain_service'` 复跑，134 项通过、12 项跳过、2 项排除。

### 遗留

- 两条 loopback 用例需在允许 IPv4 socket 的环境复验。新增回归在 Linux 模拟文件占用，本轮未在真实 Windows 安装版复验，也未运行 GitHub CI 或长期实际使用验收。
- 本地分支仍为 `codex/issue-132-h1`，基线为 `441788c`。实际 Git 元数据位于主工作树的 `.git`，当前只读，暂存时无法创建 `index.lock`。改动已保留在工作树，未提交、未推送。拟用提交标题为「Aplus：修复-更新流程-图表清理失败后可重试」。

## PR #192 P2 · 源码版更新取消提示

- 源码版更新遇到图表 IPC 清理失败时，弹出一次「暂不能更新：当前版本继续使用，请稍后重试。」主窗口保留，更新锁未预留，更新进程未准备启动。
- 共享收尾回调用 `True` 表示成功、`False` 表示 IPC 清理失败、`None` 表示其他取消。源码版仅在收到 `False` 时弹出重试提示。15 秒超时先显示原有「图表暂未关闭」警告，再用 `None` 收尾，因此源码版超时只显示一次警告。安装版仍按真假判断回调，失败时释放预留并保留下载包。
- 本改动只碰更新收尾提示路径，不加任何同步等待。按 R2 验证共享更新回调及相邻退出契约。
- 冻结校验前后一致，`verify_frozen()` 返回 `185541a4c226af123a36d113ca741c1b207a5413cd6fb6b4fd980381f0ff312c`。差异仅包含 `gui/main_window.py`、`gui/version_update.py`、`tests/test_update_flush_nonblocking.py` 和本报告。

### 本地代码测试

使用 `/home/hatch/workspace/Aplus/.venv/bin/python`。消息断言放在 `tests/test_update_flush_nonblocking.py`，复用假窗口、图表 host 和手动时钟，直接从源码版 `begin_update` 进入主窗口的实际清理与超时分支。

- 先红：在隔离目录用 `c0228d6` 的 `gui/main_window.py` 和 `gui/version_update.py` 执行 `python -m pytest -q tests/test_update_flush_nonblocking.py -k source_update_cancellation_notice`，2 项失败、1 项通过。ACK 后清理和图表已关闭时清理均触发模拟占用，但弹窗调用为零；原超时用例通过。
- 后绿：在修复工作树执行相同命令，3 项通过。两处清理失败各弹一次，超时只保留原警告。三条路径均未调用 `reserve_code_update`、`withdraw` 或 `prepare_worker`。
- 相邻回归：`python -m pytest -q tests/test_version_update.py tests/test_update_flush_nonblocking.py tests/test_installed_update_ux.py tests/test_exit_state_machine.py`，117 项通过、4 项跳过、2 项失败。失败为 `test_listening_web_port_prevents_code_update` 和 `test_real_closed_loopback_port_is_not_mistaken_for_uncertain_service`。沙箱禁止创建 IPv4 socket，两项在进入业务断言前均收到 `PermissionError: [Errno 1]`。

### 遗留

- 两条 loopback 用例待在允许 IPv4 socket 的环境复验。本轮使用模拟文件占用，真实 Windows、GitHub CI 和长期实际使用仍待验收。
- 原工作树 Git 元数据只读，无法创建 `index.lock`。改动保留在原工作树，另在临时副本的同名分支 `codex/issue-132-h1` 上以 `c0228d6` 为父提交保存交接。未推送，未新开 PR，未变更分支基线。
