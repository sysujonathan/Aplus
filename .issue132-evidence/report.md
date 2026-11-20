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
