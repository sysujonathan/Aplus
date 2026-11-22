# Issue #169 验收证据

## 布局与改动范围

盘后页的「最新研究」按钮和研究画布都挂在外层 grid 第 3 行。该行随画布拉伸，按钮在单元格内垂直居中，导致按钮底部落到画布顶部以下。盘后页将画布及滚动条整体下移一行，给摘要带独立一行。策略迭代页原已使用第 3 行放摘要、第 4 行放画布。本轮为 R1 局部布局调整。

| 文件 | 改动来源 |
| --- | --- |
| `gui/afterhours.py` | 本轮只修改研究画布挂接参数。文件内另有前三轮留下的研究记录界面改动 |
| `gui/afterhours_workspace.py` | 前序研究记录对话框与摘要带 |
| `gui/strategy_iteration.py` | 前序摘要带挂接与回测报告跳转 |
| `workbench/store.py` | 前序研究记录持久化 |
| `tests/test_backtest_history.py` | 前序历史记录契约测试 |
| `tests/test_afterhours_desktop.py` | 前序双页摘要布局和跳转测试 |

## 定向集

14 通过、1 失败、10 未选中。失败项 `test_latest_research_on_both_desktops` 在创建 Tk 窗口时遇到 `TclError: couldn't connect to display`，两页的摘要位置及运行按钮、状态栏几何断言待验证（环境）。`xvfb-run -a` 自动选取显示号。Xvfb 日志记录监听套接字创建失败，独立检查确认当前沙箱拒绝绑定文件系统和抽象 UNIX 套接字。详见 `targeted.log`。

## 冻结及交付

对指定基线 `dc234c2` 检查 `frozen_manifest.json`、`core/`、`strategies/`，Git diff 为空。当前本地 `origin/main` 与指定基线一致。由于定向集尚未全过，未达到提交门槛。原分支 Git 元数据另外受只读挂载限制，无法创建 `index.lock`。布局修复保存在原工作树。基线通过独立本地克隆创建临时 worktree。未推送、未 rebase、未开 PR。当前证据仅覆盖本地代码测试。

## Lord 验收补记（2026-11-22 02:53 自查轮）

第四轮 Codex 会话亦中途终止（PID 13363，日志停在 01:59 CST，未提交、无终版报告），
但工作树实物完整。本轮 Lord 在本机直接验收（不原样重派第五次）：

- 布局断言 `test_latest_research_on_both_desktops` 在本机 `xvfb-run -a` 下通过
  （Codex 沙箱内失败属其显示套接字绑定受限，环境原因，非代码问题）；
  `p._edit_research_note` 已补定义（gui/afterhours.py:300）。
- 定向集 `tests/test_afterhours_desktop.py` + `tests/test_backtest_history.py`：
  **24 passed、1 skipped**。
- 三文件对照（另加 `tests/test_workbench.py`）：新分支 63 passed / 2 failed /
  1 skipped（23.44s）；干净基线 `dc234c2` 同集 48 passed / 2 failed / 1 skipped
  （16.91s）；失败清单逐行 diff 为空——两项失败均为基线旧账（已在干净基线
  worktree 复现相同失败）。新增 15 项通过，耗时增量来自新增测试用例本身。
- `git diff --check` 通过；冻结文件（`frozen_manifest.json`、`core/`、
  `strategies/`）diff 为空；`tests/test_backtest_history.py` diff 零删除，
  存量 display 全等断言未改且全过。
- 补齐任务书缺项：新增独立验收文档
  `docs/验收/2026-11-22-回测研究记录与最新研究摘要.md`（改动、验收步骤与
  结果、遗留）；耗时对照见该文档。
- 结论：验收通过。提交 `Aplus：功能-回测研究-研究记录位与最新研究摘要`，
  推送分支 `codex/issue-169-research-note` 并开 PR（Closes #169，合并后仍须
  核实合并后 main 验收转绿方可按规则关闭 Issue）。
