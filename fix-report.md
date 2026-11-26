# Store 崩溃后立即重启修复

盘前及其他 Store 入口在前一进程留下 SQLite 回滚日志时，可在首次启动中完成恢复。此次为 R2 持久化启动修复，基线为 `98a9c5a`，负责人为 Codex，Lord 负责验收和提 PR。

## 根因与改动

原版 `Store.__init__` 在进入初始化写事务前调用 `_pa_migration_backup()`。当崩溃留下 hot journal 时，SQLite 的第一条读查询需要先写回回滚页。原版以 `mode=ro` 执行该查询，因此在 `workbench/store.py:208` 当场退出，与任务提供的盘前日志一致。

对已有库，Store 先用 `mode=rw` 连接并执行 `PRAGMA schema_version`，让 SQLite 实际读取库并自行完成恢复。连接随后关闭，继续原版只读备份和版本检查。新库沿用原启动流程。恢复查询只读取 schema 版本，业务写入和迁移仍由原有代码执行。改动仅在启动路径，扫描和图表热路径没有变化。冻结策略和清单逐字节保留。

## 修前复现与修后验活

复现只使用临时目录里的合成 Store。测试通过子进程强制脏页落盘，再用 `os._exit(17)` 中断写事务。下一行初始化 Store，过程无等待或重试。将本次新增测试复制到基线检出后运行：

```sh
python -m pytest -q tests/test_store_restart_recovery.py --basetemp=/tmp/aplus-rofix-before
```

修改生产代码前实际输出：

```text
workbench/store.py:62: in __init__
    self._pa_migration_backup()
workbench/store.py:208: in _pa_migration_backup
    has_meta = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
E   sqlite3.OperationalError: attempt to write a readonly database
1 failed, 2 passed in 0.36s
```

修后直接打开修前失败目录中 `workbench.sqlite3` 和 `workbench.sqlite3-journal` 的逐字节副本。以下命令可用于独立复核，先保留源样本，再从修后的检出运行。命令输出中的哈希按每次合成样本变化。

```sh
scratch=$(mktemp -d)
cp /tmp/aplus-rofix-before/test_restart_recovers_hot_jour0/workbench.sqlite3* "$scratch/"
sha256sum "$scratch"/workbench.sqlite3*
python - "$scratch" <<'PY'
import sys
from workbench.store import Store

store = Store(sys.argv[1])
assert store.rows('SELECT id, value FROM restart_probe') == [{'id': 1, 'value': 'committed'}]
assert store.rows('PRAGMA integrity_check') == [{'integrity_check': 'ok'}]
assert not (store.root / 'workbench.sqlite3-journal').exists()
assert not (store.root / 'backups').exists()
print('Store OK; committed row only; integrity_check=ok; journal removed; no v9 backup')
PY
```

实际样本的 SHA-256 分别为：

- 数据库 `20526b03c3bf6c0f5ba2cfa5054b0f41ffb5639218d56ac96545ef57788f8fd0`
- rollback journal `fd51816441383f765cb61e894f73e2c3f8d7729184ec52b977d7691e95e0a181`

副本首次启动成功。已提交行内容正确，临时更新和临时插入均已回滚，日志消失，完整性检查返回 `ok`。回归还验证了 v8 生成可读的 pre-pa 备份后升级、v9 无备份，以及未来版本拒绝时保留原始库字节和完整原错误文本。

## Store 启动耗时

Linux，Python 3.12.3，SQLite 3.45.1。使用上述恢复成功后的同一份 262,144 字节 v9 合成库。每次计时前复制同一份文件至同一路径，用 `perf_counter_ns()` 只包住 `Store(root)`，前后代码交替先跑，各测 11 次。

| 版本 | 样本（毫秒） | 中位数（毫秒） |
| --- | --- | ---: |
| 修前 | 1.885, 2.310, 1.643, 1.464, 1.417, 1.465, 1.715, 1.498, 1.467, 1.602, 1.418 | 1.498 |
| 修后 | 1.598, 1.499, 1.977, 1.587, 1.476, 2.052, 1.490, 1.432, 1.438, 1.475, 1.434 | 1.490 |

实测差值为 −0.008 ms，处于样本波动内。新增工作为一个短连接和一次 schema 读取。热日志恢复耗时取决于崩溃事务规模。

## 其他只读连接

以下是代码检查发现的候选点。若它们先于可写方读取带 hot journal 的库，也可能遭遇 SQLite 只读恢复错误。各调用路径的错误处理不同，本次未修改这些入口。

| 位置 | 读取对象 |
| --- | --- |
| `gui/background_status.py:96` | 桌面后台状态库查询 |
| `gui/startup_reads.py:17` | 桌面首屏后台读会话 |
| `workbench/watch_history.py:83` | 关注行情查询 |
| `workbench/premarket_store.py:51` | 盘前状态及检查点查询 |
| `workbench/snapshot.py:55` | 只读快照及案例导出适配器 |
| `workbench/acceleration_profile.py:63` | 加速配置状态查询 |
| `workbench/installed_data.py:57,170` | 安装版数据库识别及备份核验 |
| `workbench/installed_migration.py:17,55` | 安装版迁移的版本读取及影子库核验 |
| `workbench/installed_backup_rotation.py:125` | 安装版备份核验 |
| `workbench/version_update.py:1022,1069` | 升级前快照与数据库状态检查 |

工具入口也有直接的 `mode=ro` 查询，分布于 `tools/validate_three_k_fast_reject.py`、`tools/validate_mtr_shadow.py`、`tools/benchmark_tickflow_parallel.py`、`tools/benchmark_startup.py`、`tools/benchmark_h2_replay.py`、`tools/schema_migration_probe.py`、`tools/maintenance_update.py`、`tools/acceptance_stop_research.py` 和 `tools/acceptance_h2_replay.py`。其中影子库、备份和探针仅在其实际输入也带热日志时受影响。

## 回归与交付状态

- 定向命令 `python -m pytest -q tests/test_store_restart_recovery.py tests/test_pa_growth_store.py`，20 项全部通过。
- 扩展命令 `python -m pytest -q tests/test_store_restart_recovery.py tests/test_workbench.py tests/test_pa_growth_store.py tests/test_premarket_*.py tests/test_startup_*.py tests/test_installed_data_recovery.py tests/test_installed_schema_bridge.py`，265 通过、20 跳过、26 失败。两项 Windows 运行目录预期在原版 `98a9c5a` 的 Linux 环境也复现失败。另有 18 项 Tk 用例因无显示会话失败，6 项安装交接用例因 Linux 无 `ctypes.windll` 失败。尝试 Xvfb 后 Tk 仍无法连接显示。
- Linux 可运行的 workbench、盘前和只读启动集通过 182 项、跳过 3 项、排除 2 项已在基线复现的 Windows 路径用例。命令见下方。
- 本地证据全部来自合成数据。真实盘前运行、Windows 桌面验收、GitHub CI 和跨交易日验收待执行。
- GitHub 连接器确认远端 `main` 为 `98a9c5a`，相关 Store 活动 PR 查询为空。原工作树的 Git 元数据为只读，无法创建 `index.lock`。交付提交使用同基线临时克隆，原工作树保留三个交付文件。
- 分支推送受环境阻断。Git HTTPS 代理无法连接，GitHub 连接器写入因当前审批策略被拒绝。交付包 `/tmp/aplus-rofix-delivery.bundle` 包含指定分支上的提交，前置基线为 `98a9c5a`。Lord 可在原工作树的 Git 元数据可写时取用。没有创建 PR。

```sh
python -m pytest -q tests/test_store_restart_recovery.py tests/test_pa_growth_store.py \
  tests/test_workbench.py tests/test_premarket_checkpoints.py tests/test_premarket_daily_first.py \
  tests/test_premarket_flow.py tests/test_premarket_resume.py tests/test_premarket_snapshot.py \
  tests/test_premarket_validation_speed.py tests/test_startup_reads.py \
  -k 'not test_default_runtime_is_stable_per_user_without_configuration and not test_default_runtime_preserves_existing_checkout_data'
```
