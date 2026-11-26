# PR #195 窄窗撤销入口修复记录

按 `docs/Codex执行规范.md` 初判为 R1，影响 Tk 兼容图表顶栏。已读取 Jonathan 的 CHANGES_REQUESTED 评审及 P2 内联评论。评审记录显示，125% / 1000×700 下撤销从完整显示变为不可见，200% / 1490×700 下从完整显示被压至 13 像素。[评审全文](https://github.com/sysujonathan/Aplus/pull/195#pullrequestreview-6076721426) · [P2 内联评论](https://github.com/sysujonathan/Aplus/pull/195#discussion_r4710413306)

按任务书的路径异常条款停工。指定工作树处于 detached HEAD `9877b98`，目标本地分支 `codex/ui-visual-implementation` 已由 `/home/hatch/workspace/Aplus-wt-uivis-impl` 占用，检出的是 `47fd109`。指定工作树的实际 Git 元数据在 `/home/hatch/workspace/Aplus/.git`，位于本会话可写范围之外。Git HTTPS 查询也因代理无法连接而失败。本地缓存的 `origin/main` 为 `fbf0b400`，未通过此次 Git 查询确认远端状态。

| 验收项 | 已有证据 | 本轮状态 |
| --- | --- | --- |
| 125% / 1000×700 撤销完整显示 | Jonathan 的 Windows 对照记录 | 未复跑 |
| 200% / 1490×700 撤销完整显示 | Jonathan 的 Windows 对照记录 | 未复跑 |
| 其余按钮可见与现有视觉 | 待验证 | 未复跑 |
| 新增窗口缩放回归红绿 | 待实现 | 未执行 |
| Windows 定向测试与 A acceptance | 评审记录原提交定向测试 97 项通过、A acceptance #705 通过 | 未产生新提交或新 CI 结果 |

本轮交付仅有此记录。GUI、测试和 Git 分支均未修改，没有提交或推送。

---

## Lord 验收补记（2026-11-27 02:5x，第三轮会话中途终止后由 Lord 直接验收工作树交付）

- 第三轮修复会话（2026-11-27 01:53 派出，PID 10905）于约 02:00 CST 无征兆终止：进程不存在，日志 `codex-195-p2fix-resume2-20261127.log` 末行停在测试输出，无收尾说明、无报告更新（本文件仍为第一轮停工记录）。工作树内改动即交付，已保留。
- 改动内容（2 文件，103 增/14 减）：`gui/chart_workspace.py`——顶栏拆为左侧时段区（periods）与右侧操作区（actions）两个 Frame，新增 `fit_topbar`：两区宽度之和超过顶栏宽时操作区换到第二行（`side=tk.BOTTOM`，顶栏加高），否则保持单行；撤销/重做按钮随操作区整体换行，不再被挤出。`tests/test_chart_workspace.py`——沿用第二轮已写好的回归测试 `test_compat_chart_toolbar_remains_reachable_when_resized`（125% / 1000、200% / 1490 两档参数化），未重写。
- 先红后绿证据（日志内）：修复前两档新测试均失败（`2 failed, 48 deselected`）；修复后完整四文件集一轮 `1 failed, 104 passed, 6 skipped, 2 deselected`——仅 `test_native_fallback…` 失败，两档新测试均通过；该轮结果在日志内出现两次。
- Lord 本机复核（工作树 Aplus-wt-195fix，Aplus venv，`xvfb-run -a`）：① 四文件集 `2 failed, 105 passed, 6 skipped`——125% 档通过，200% 档失败原因见下，`test_native_fallback…` 失败原因见下；② 200% 档单独跑两次均通过（`1 passed`）；③ 基线对照（仅 `git stash` gui 改动后同集重跑）：`3 failed, 104 passed, 6 skipped`——两档新测试均失败（红），`test_native_fallback…` 同样失败。结论：失败集合与基线一致、无新增失败；修复有效。
- 两处失败均属本机 Linux 环境问题、非修复缺陷：① 200% 档在成组跑时抛 `TclError: can't invoke "winfo" command: application has been destroyed`（ttkbootstrap Style 单例在上一测试根窗口销毁后回调已销毁应用），单独跑通过；② `test_native_fallback_reads_web_text_and_ruler_without_changing_saved_objects` 同为该类 TclError，修复前后失败表现一致，属既有环境失败。
- 冻结摘要：`verify_frozen()` = `185541a4c226af123a36d113ca741c1b207a5413cd6fb6b4fd980381f0ff312c`，与已知值一致；`core/strategies/` 与 `frozen_manifest.json` 未动。
- 未验证事项：真实 Windows 窄窗（125% / 1000×700、200% / 1490×700）下的目检与点击，待 Jonathan 在 Windows 侧复审确认；分支仍落后 main（#191/#192/#193/#194 已合并），是否更新由 Jonathan 决定。
- 处置：Lord 已提交并推送到同一 PR #195（分支 `codex/ui-visual-implementation`），请 Jonathan 复审。
