# 增补二 · 桌面工具图标统一草案

标的查找、翻页、图表工具和成长空间共用同一套工具图标。各按钮原有含义、操作和状态继续沿用。基于本地 `5ec7691` 静态盘点，以下行号均指该版本。配套审阅 [离线样张](icons.html)。

## 盘点口径

按「源资产／同一来源的状态字形」计，共 **174 条**，归一为 **83 个工具图标**，另保留 **1 个品牌资产**。同模块重复引用同一资产只计一次。布局行列参数、绘图颜色预览和图格编号是参数或控件内容。K 线策略标记、用户可选的图表标记内容、框架原生复选框与下拉控件字形沿原语义使用。成长空间的 21 个 SVG 定义全部入表，包括源码预留项。

- S 为显示 DPI ÷ 96。Tk-A 是 `workspace_icons.py:71` 的 #B8BBC2。Tk-B 是 `workspace_toolbar.py:161` 的常态 #C4C6CB、禁用 #555B63、锁存 #171717。Tk 的主题字形点数和实际像素由系统与主题决定。
- 成长 SVG 使用 24 网格、1.65 单位描边，继承控件的 currentColor。基础 TEXT #ECF3FC、MUTED #95A5BC，角色色和徽记例外见 `growth.css:1–2,24`。
- TV-A 为默认 23 px（光标菜单 20 px、弹窗关闭 18 px 宽、小窗口收藏 20 px、对象树锁 15 px），1.55 单位描边，继承前景。上条 #D1D4DC，弱态 #56585E，绘图条 #C7C9CE／选中 #FFF，磁铁锁存 #111，收藏 #DBDBDB。TV-B 为图表导航／回放的 currentColor，导航 1.8、回放 1.4 单位描边。TV-C 为对象快条 currentColor、1.4 单位描边。TV-D 为继承控件字号与前景的文本字形，顶栏 caret 13 px、绘图子菜单 9 px，均用 #787B86。详见 `prototype.css:33,83–99,144–163,208–218,505,579`。

| 来源 | 现有图标／符号 | 现尺寸 | 现颜色 | 归一名称 |
|---|---|---|---|---|
| [workspace_icons.py:10](../gui/workspace_icons.py#L10) | candle | 22×S px | Tk-A | `candle` |
| [workspace_icons.py:12](../gui/workspace_icons.py#L12) | chartLine | 22×S px | Tk-A | `chart-line` |
| [workspace_icons.py:13](../gui/workspace_icons.py#L13) | indicators | 22×S px | Tk-A | `indicator` |
| [workspace_icons.py:15](../gui/workspace_icons.py#L15) | alert | 22×S px | Tk-A | `alarm` |
| [workspace_icons.py:16](../gui/workspace_icons.py#L16) | replay | 22×S px | Tk-A | `replay` |
| [workspace_icons.py:17](../gui/workspace_icons.py#L17) | undo | 22×S px | Tk-A | `undo` |
| [workspace_icons.py:18](../gui/workspace_icons.py#L18) | redo | 22×S px | Tk-A | `redo` |
| [workspace_icons.py:19](../gui/workspace_icons.py#L19) | compare | 22×S px | Tk-A | `compare` |
| [workspace_icons.py:20](../gui/workspace_icons.py#L20) | camera | 22×S px | Tk-A | `camera` |
| [workspace_icons.py:21](../gui/workspace_icons.py#L21) | search | 22×S px | Tk-A | `quick-search` |
| [workspace_icons.py:22](../gui/workspace_icons.py#L22) | settings | 22×S px | Tk-A | `settings` |
| [workspace_icons.py:23](../gui/workspace_icons.py#L23) | fullscreen | 22×S px | Tk-A | `fullscreen` |
| [workspace_icons.py:24](../gui/workspace_icons.py#L24) | restore | 22×S px | Tk-A | `restore` |
| [workspace_icons.py:25](../gui/workspace_icons.py#L25) | idea | 22×S px | Tk-A | `idea` |
| [workspace_icons.py:27](../gui/workspace_icons.py#L27) | sidebar | 22×S px | Tk-A | `panel` |
| [workspace_icons.py:28](../gui/workspace_icons.py#L28) | cross | 24×S px | Tk-B | `cross` |
| [workspace_icons.py:29](../gui/workspace_icons.py#L29) | cursor-arrow | 24×S px | Tk-B | `pointer` |
| [workspace_icons.py:30](../gui/workspace_icons.py#L30) | cursor-eraser | 24×S px | Tk-B | `eraser` |
| [workspace_icons.py:31](../gui/workspace_icons.py#L31) | trend | 24×S px | Tk-B | `trend` |
| [workspace_icons.py:32](../gui/workspace_icons.py#L32) | ray | 24×S px | Tk-B | `ray` |
| [workspace_icons.py:33](../gui/workspace_icons.py#L33) | fib | 24×S px | Tk-B | `fib` |
| [workspace_icons.py:34](../gui/workspace_icons.py#L34) | patterns | 24×S px | Tk-B | `patterns` |
| [workspace_icons.py:35](../gui/workspace_icons.py#L35) | path | 24×S px | Tk-B | `polyline` |
| [workspace_icons.py:36](../gui/workspace_icons.py#L36) | brush | 24×S px | Tk-B | `brush` |
| [workspace_icons.py:37](../gui/workspace_icons.py#L37) | stay | 24×S px | Tk-B | `stay` |
| [workspace_icons.py:38](../gui/workspace_icons.py#L38) | long | 24×S px | Tk-B | `long` |
| [workspace_icons.py:39](../gui/workspace_icons.py#L39) | short | 24×S px | Tk-B | `short` |
| [workspace_icons.py:40](../gui/workspace_icons.py#L40) | rectangle | 24×S px | Tk-B | `rectangle` |
| [workspace_icons.py:41](../gui/workspace_icons.py#L41) | up | 24×S px | Tk-B | `up` |
| [workspace_icons.py:42](../gui/workspace_icons.py#L42) | down | 24×S px | Tk-B | `down` |
| [workspace_icons.py:43](../gui/workspace_icons.py#L43) | arrow | 24×S px | Tk-B | `drawing-arrow` |
| [workspace_icons.py:44](../gui/workspace_icons.py#L44) | channel | 24×S px | Tk-B | `channel` |
| [workspace_icons.py:45](../gui/workspace_icons.py#L45) | text | 24×S px | Tk-B | `text` |
| [workspace_icons.py:46](../gui/workspace_icons.py#L46) | ruler | 24×S px | Tk-B | `ruler` |
| [workspace_icons.py:47](../gui/workspace_icons.py#L47) | zoom | 24×S px | Tk-B | `zoom` |
| [workspace_icons.py:48](../gui/workspace_icons.py#L48) | zoomOut | 24×S px | Tk-B | `zoom-out` |
| [workspace_icons.py:49](../gui/workspace_icons.py#L49) | magnet | 24×S px | Tk-B | `magnet` |
| [workspace_icons.py:50](../gui/workspace_icons.py#L50) | lock | 24×S px | Tk-B | `lock` |
| [workspace_icons.py:51](../gui/workspace_icons.py#L51) | eye | 24×S px | Tk-B | `eye` |
| [workspace_icons.py:52](../gui/workspace_icons.py#L52) | trash | 24×S px | Tk-B | `trash` |
| [workspace_icons.py:53](../gui/workspace_icons.py#L53) | star | 24×S px | Tk-B | `star` |
| [workspace_icons.py:56](../gui/workspace_icons.py#L56) | cursor-dot | 24×S px | Tk-B | `dot` |
| [workspace_icons.py:56](../gui/workspace_icons.py#L56) | cursor-highlight | 24×S px | Tk-B | `highlight` |
| [workspace_icons.py:67](../gui/workspace_icons.py#L67) | smile | 24×S px | Tk-B | `smile` |
| [workspace_icons.py:76](../gui/workspace_icons.py#L76) | badge | 24×S px | 描边 #83858A | `pane-badge` |
| [workspace_icons.py:76](../gui/workspace_icons.py#L76) | activeBadge | 24×S px | #ECECEC 填充／#83858A 边 | `pane-badge` |
| [workspace_icons.py:88](../gui/workspace_icons.py#L88) | (rows,columns) | 24／32×S px | Tk-A | `layout` |
| [toolbar.py:405](../gui/toolbar.py#L405) | ‹ | 主题字体 | ttk primary | `chevron-left` |
| [toolbar.py:405](../gui/toolbar.py#L405) | › | 主题字体 | ttk primary | `chevron-right` |
| [workspace_toolbar.py:190](../gui/workspace_toolbar.py#L190) | # | Segoe UI 9／10 pt | 标记 #777C84／箭头 MUTED | `availability` |
| [workspace_toolbar.py:190](../gui/workspace_toolbar.py#L190) | › | Segoe UI 9／10 pt | 标记 #777C84／箭头 MUTED | `chevron-right` |
| [workspace_toolbar.py:270](../gui/workspace_toolbar.py#L270) | ★ | Segoe UI 12 pt | #B8BBC2 | `star` |
| [workspace_toolbar.py:270](../gui/workspace_toolbar.py#L270) | ☆ | Segoe UI 12 pt | #B8BBC2 | `star` |
| [workspace_controls.py:295](../gui/workspace_controls.py#L295) | ⌄ | Tk 默认字体 | TEXT | `chevron-down` |
| [workspace_controls.py:128](../gui/workspace_controls.py#L128) | ⋯ | 菜单字体 | TEXT | `more` |
| [workspace_controls.py:386](../gui/workspace_controls.py#L386) | ★ | Tk 默认字体 | TEXT／MUTED | `star` |
| [workspace_controls.py:386](../gui/workspace_controls.py#L386) | ☆ | Tk 默认字体 | TEXT／MUTED | `star` |
| [workspace_toolbar.py:145](../gui/workspace_toolbar.py#L145) | 六点拖柄 | 11×18×S px 画布 | 点 #686B71 | `drag-handle` |
| [chart_workspace.py:189](../gui/chart_workspace.py#L189) | ⏪ | Tk 默认字体 | TEXT | `replay` |
| [chart_workspace.py:189](../gui/chart_workspace.py#L189) | 📷 | Tk 默认字体 | TEXT | `camera` |
| [chart_workspace.py:189](../gui/chart_workspace.py#L189) | 闹钟＋ | Tk 默认字体 | TEXT | `alarm` |
| [chart_workspace.py:189](../gui/chart_workspace.py#L189) | ↶ | Tk 默认字体 | TEXT | `undo` |
| [chart_workspace.py:189](../gui/chart_workspace.py#L189) | ↷ | Tk 默认字体 | TEXT | `redo` |
| [chart_workspace.py:189](../gui/chart_workspace.py#L189) | ◀ | Tk 默认字体 | TEXT | `step-back` |
| [chart_workspace.py:189](../gui/chart_workspace.py#L189) | ▶ | Tk 默认字体 | TEXT | `play` |
| [chart_workspace.py:189](../gui/chart_workspace.py#L189) | ▶&#124; | Tk 默认字体 | TEXT | `step-forward` |
| [chart_workspace.py:788](../gui/chart_workspace.py#L788) | Ⅱ | Tk 默认字体 | TEXT | `pause` |
| [chart_workspace.py:223](../gui/chart_workspace.py#L223) | ✚ | Tk 默认字体 | TEXT／拖柄 MUTED | `cross` |
| [chart_workspace.py:223](../gui/chart_workspace.py#L223) | ◉ | Tk 默认字体 | TEXT／拖柄 MUTED | `eye` |
| [chart_workspace.py:223](../gui/chart_workspace.py#L223) | ♲ | Tk 默认字体 | TEXT／拖柄 MUTED | `trash` |
| [chart_workspace.py:223](../gui/chart_workspace.py#L223) | ⠿ | Tk 默认字体 | TEXT／拖柄 MUTED | `drag-handle` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | ≋ | Tk 默认字体 | TEXT | `fib` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | L↑ | Tk 默认字体 | TEXT | `long` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | ↦ | Tk 默认字体 | TEXT | `ray` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | ╱ | Tk 默认字体 | TEXT | `trend` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | S↓ | Tk 默认字体 | TEXT | `short` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | □ | Tk 默认字体 | TEXT | `rectangle` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | ↑ | Tk 默认字体 | TEXT | `up` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | ↓ | Tk 默认字体 | TEXT | `down` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | ➚ | Tk 默认字体 | TEXT | `drawing-arrow` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | ⌁ | Tk 默认字体 | TEXT | `polyline` |
| [chart_workspace.py:32](../gui/chart_workspace.py#L32) | ∥ | Tk 默认字体 | TEXT | `channel` |
| [watch_panel.py:130](../gui/watch_panel.py#L130) | × | 主题字体 | ttk secondary | `close` |
| [date_picker.py:82](../gui/date_picker.py#L82) | ✕ | 主题字体 | ttk secondary-outline | `close` |
| [afterhours.py:99](../gui/afterhours.py#L99) | ← | 主题字体 | ttk secondary | `chevron-left` |
| [trade_management.py:1966](../gui/trade_management.py#L1966) | ＋ | 主题字体 | ttk primary-outline／secondary-outline | `plus` |
| [trade_management.py:1966](../gui/trade_management.py#L1966) | − | 主题字体 | ttk primary-outline／secondary-outline | `minus` |
| [icon_aplus.ico](../icon_aplus.ico) | 应用／窗口图标 | ICO 多尺寸 | 品牌 #111214／#FFFFFF／#FFA578 | `品牌资产` |
| [growth.js:8](../gui/pa_growth/growth.js#L8) | trial | 17 px | 成长 | `trial` |
| [growth.js:9](../gui/pa_growth/growth.js#L9) | library | 17 px | 成长 | `library` |
| [growth.js:10](../gui/pa_growth/growth.js#L10) | universe | 17／48 px | 成长／徽记 #C1D5E7 | `universe` |
| [growth.js:11](../gui/pa_growth/growth.js#L11) | growth | 17 px | 成长 | `growth` |
| [growth.js:12](../gui/pa_growth/growth.js#L12) | search | 17 px | #7897BB | `search` |
| [growth.js:13](../gui/pa_growth/growth.js#L13) | plus | 17 px | 成长 | `plus` |
| [growth.js:13](../gui/pa_growth/growth.js#L13) | minus | 17 px | 成长 | `minus` |
| [growth.js:14](../gui/pa_growth/growth.js#L14) | close | 17 px | 成长 | `close` |
| [growth.js:14](../gui/pa_growth/growth.js#L14) | back | 17 px | 成长 | `chevron-left` |
| [growth.js:15](../gui/pa_growth/growth.js#L15) | upload | 17 px | 成长 | `upload` |
| [growth.js:16](../gui/pa_growth/growth.js#L16) | paste | 17 px | 成长 | `paste` |
| [growth.js:17](../gui/pa_growth/growth.js#L17) | image | 17／27 px | 成长 | `image` |
| [growth.js:18](../gui/pa_growth/growth.js#L18) | zoom | 17 px | 成长 | `zoom` |
| [growth.js:19](../gui/pa_growth/growth.js#L19) | arrow | 17 px | 成长 | `arrow-right` |
| [growth.js:19](../gui/pa_growth/growth.js#L19) | filter | 17 px | 成长 | `filter` |
| [growth.js:20](../gui/pa_growth/growth.js#L20) | note | 17 px | 成长 | `note` |
| [growth.js:20](../gui/pa_growth/growth.js#L20) | edit | 17 px | 成长 | `edit` |
| [growth.js:21](../gui/pa_growth/growth.js#L21) | layers | 17 px | 成长 | `layers` |
| [growth.js:22](../gui/pa_growth/growth.js#L22) | check | 17 px | 成长 | `check` |
| [growth.js:22](../gui/pa_growth/growth.js#L22) | pin | 17 px | 成长 | `pin` |
| [growth.js:23](../gui/pa_growth/growth.js#L23) | lock | 17 px | 成长 | `lock` |
| [index.html:11](../docs/prototypes/tv-workspace/index.html#L11) | i-cross | TV-A／24 网格 | TV-A | `cross` |
| [index.html:12](../docs/prototypes/tv-workspace/index.html#L12) | i-dot | TV-A／24 网格 | TV-A | `dot` |
| [index.html:13](../docs/prototypes/tv-workspace/index.html#L13) | i-pointer | TV-A／24 网格 | TV-A | `pointer` |
| [index.html:14](../docs/prototypes/tv-workspace/index.html#L14) | i-highlight | TV-A／24 网格 | TV-A | `highlight` |
| [index.html:15](../docs/prototypes/tv-workspace/index.html#L15) | i-eraser | TV-A／24 网格 | TV-A | `eraser` |
| [index.html:16](../docs/prototypes/tv-workspace/index.html#L16) | i-trend | TV-A／24 网格 | TV-A | `trend` |
| [index.html:17](../docs/prototypes/tv-workspace/index.html#L17) | i-fib | TV-A／24 网格 | TV-A | `fib` |
| [index.html:18](../docs/prototypes/tv-workspace/index.html#L18) | i-path | TV-A／24 网格 | TV-A | `shape-path` |
| [index.html:19](../docs/prototypes/tv-workspace/index.html#L19) | i-polyline | TV-A／28 网格 | TV-A | `polyline` |
| [index.html:20](../docs/prototypes/tv-workspace/index.html#L20) | i-text | TV-A／24 网格 | TV-A | `text` |
| [index.html:21](../docs/prototypes/tv-workspace/index.html#L21) | i-long | TV-A／28 网格 | TV-A | `long` |
| [index.html:22](../docs/prototypes/tv-workspace/index.html#L22) | i-short | TV-A／28 网格 | TV-A | `short` |
| [index.html:23](../docs/prototypes/tv-workspace/index.html#L23) | i-ray | TV-A／28 网格 | TV-A | `ray` |
| [index.html:24](../docs/prototypes/tv-workspace/index.html#L24) | i-rectangle | TV-A／28 网格 | TV-A | `rectangle` |
| [index.html:25](../docs/prototypes/tv-workspace/index.html#L25) | i-up | TV-A／28 网格 | TV-A | `up` |
| [index.html:26](../docs/prototypes/tv-workspace/index.html#L26) | i-down | TV-A／28 网格 | TV-A | `down` |
| [index.html:27](../docs/prototypes/tv-workspace/index.html#L27) | i-arrow | TV-A／28 网格 | TV-A | `drawing-arrow` |
| [index.html:28](../docs/prototypes/tv-workspace/index.html#L28) | i-channel | TV-A／28 网格 | TV-A | `channel` |
| [index.html:29](../docs/prototypes/tv-workspace/index.html#L29) | i-smile | TV-A／24 网格 | TV-A | `smile` |
| [index.html:30](../docs/prototypes/tv-workspace/index.html#L30) | i-ruler | TV-A／24 网格 | TV-A | `ruler` |
| [index.html:31](../docs/prototypes/tv-workspace/index.html#L31) | i-zoom | TV-A／24 网格 | TV-A | `zoom` |
| [index.html:32](../docs/prototypes/tv-workspace/index.html#L32) | i-zoom-out | TV-A／24 网格 | TV-A | `zoom-out` |
| [index.html:33](../docs/prototypes/tv-workspace/index.html#L33) | i-magnet | TV-A／24 网格 | TV-A | `magnet` |
| [index.html:34](../docs/prototypes/tv-workspace/index.html#L34) | i-lock | TV-A／24 网格 | TV-A | `lock` |
| [index.html:35](../docs/prototypes/tv-workspace/index.html#L35) | i-eye | TV-A／24 网格 | TV-A | `eye` |
| [index.html:36](../docs/prototypes/tv-workspace/index.html#L36) | i-trash | TV-A／24 网格 | TV-A | `trash` |
| [index.html:37](../docs/prototypes/tv-workspace/index.html#L37) | i-candle | TV-A／24 网格 | TV-A | `candle` |
| [index.html:38](../docs/prototypes/tv-workspace/index.html#L38) | i-indicator | TV-A／24 网格 | TV-A | `indicator` |
| [index.html:39](../docs/prototypes/tv-workspace/index.html#L39) | i-layout | TV-A／24 网格 | TV-A | `layout` |
| [index.html:40](../docs/prototypes/tv-workspace/index.html#L40) | i-alarm | TV-A／24 网格 | TV-A | `alarm` |
| [index.html:41](../docs/prototypes/tv-workspace/index.html#L41) | i-replay | TV-A／24 网格 | TV-A | `replay` |
| [index.html:42](../docs/prototypes/tv-workspace/index.html#L42) | i-undo | TV-A／24 网格 | TV-A | `undo` |
| [index.html:43](../docs/prototypes/tv-workspace/index.html#L43) | i-redo | TV-A／24 网格 | TV-A | `redo` |
| [index.html:44](../docs/prototypes/tv-workspace/index.html#L44) | i-camera | TV-A／24 网格 | TV-A | `camera` |
| [index.html:45](../docs/prototypes/tv-workspace/index.html#L45) | i-panel | TV-A／24 网格 | TV-A | `panel` |
| [index.html:46](../docs/prototypes/tv-workspace/index.html#L46) | i-more | TV-A／24 网格 | TV-A | `more` |
| [index.html:47](../docs/prototypes/tv-workspace/index.html#L47) | i-settings | TV-A／24 网格 | TV-A | `settings` |
| [index.html:48](../docs/prototypes/tv-workspace/index.html#L48) | i-close | TV-A／24 网格 | TV-A | `close` |
| [index.html:49](../docs/prototypes/tv-workspace/index.html#L49) | i-star | TV-A／24 网格 | TV-A | `star` |
| [index.html:161](../docs/prototypes/tv-workspace/index.html#L161) | navZoomOut | 18 px 导航 | TV-B | `minus` |
| [index.html:162](../docs/prototypes/tv-workspace/index.html#L162) | navZoomIn | 18 px 导航 | TV-B | `plus` |
| [index.html:163](../docs/prototypes/tv-workspace/index.html#L163) | navMoveLeft | 18 px 导航 | TV-B | `chevron-left` |
| [index.html:164](../docs/prototypes/tv-workspace/index.html#L164) | navMoveRight | 18 px 导航 | TV-B | `chevron-right` |
| [index.html:165](../docs/prototypes/tv-workspace/index.html#L165) | navReset | 18 px 导航 | TV-B | `reset-view` |
| [index.html:172](../docs/prototypes/tv-workspace/index.html#L172) | replayDatePicker | 20 px 回放 | TV-B | `replay-date` |
| [index.html:174](../docs/prototypes/tv-workspace/index.html#L174) | replayPlay | 20 px 回放 | TV-B | `play` |
| [index.html:175](../docs/prototypes/tv-workspace/index.html#L175) | replayStep | 20 px 回放 | TV-B | `step-forward` |
| [index.html:179](../docs/prototypes/tv-workspace/index.html#L179) | replayToLatest | 20 px 回放 | TV-B | `step-latest` |
| [drawing-quickbar.js:123](../docs/prototypes/tv-workspace/drawing-quickbar.js#L123) | template | 23 px | TV-C | `drawing-template` |
| [drawing-quickbar.js:124](../docs/prototypes/tv-workspace/drawing-quickbar.js#L124) | lineColor | 23 px | TV-C | `line-color` |
| [drawing-quickbar.js:125](../docs/prototypes/tv-workspace/drawing-quickbar.js#L125) | fillColor | 23 px | TV-C | `fill-color` |
| [drawing-quickbar.js:126](../docs/prototypes/tv-workspace/drawing-quickbar.js#L126) | textColor | 23 px | TV-C | `text` |
| [drawing-quickbar.js:127](../docs/prototypes/tv-workspace/drawing-quickbar.js#L127) | order | 23 px | TV-C | `order` |
| [prototype-interactions.js:2566](../docs/prototypes/tv-workspace/prototype-interactions.js#L2566) | 暂停 | 20 px | TV-B | `pause` |
| [index.html:55](../docs/prototypes/tv-workspace/index.html#L55) | ⌄ | TV-D | TV-D | `chevron-down` |
| [index.html:102](../docs/prototypes/tv-workspace/index.html#L102) | › | TV-D | TV-D | `chevron-right` |
| [index.html:309](../docs/prototypes/tv-workspace/index.html#L309) | ‹ | TV-D | TV-D | `chevron-left` |
| [index.html:250](../docs/prototypes/tv-workspace/index.html#L250) | × | TV-D | TV-D | `close` |
| [index.html:276](../docs/prototypes/tv-workspace/index.html#L276) | ✎ | TV-D | TV-D | `edit` |
| [prototype-interactions.js:2259](../docs/prototypes/tv-workspace/prototype-interactions.js#L2259) | ★ | TV-D | TV-D | `star` |
| [prototype-interactions.js:2259](../docs/prototypes/tv-workspace/prototype-interactions.js#L2259) | ☆ | TV-D | TV-D | `star` |
| [prototype.css:144](../docs/prototypes/tv-workspace/prototype.css#L144) | 六点拖柄 | 3 px 点径 | 收藏 #575757／对象 #666 | `drag-handle` |
| [index.html:129](../docs/prototypes/tv-workspace/index.html#L129) | ⋮ | TV-D | #787B86 | `more` |
| [index.html:130](../docs/prototypes/tv-workspace/index.html#L130) | ◉̸ | TV-D | #787B86 | `eye-off` |
| [production-controls.js:433](../docs/prototypes/tv-workspace/production-controls.js#L433) | ✓ | 菜单继承 | 菜单继承 | `check` |

## 统一草案

| 档位 | 使用场景 | 线宽 | 四边留白目标 |
|---|---|---|---|
| 16 px | 密集列表、次级翻页和菜单 | 1.25 px | 1 px |
| 20 px | 常用按钮、图表顶条 | 1.50 px | 1.5 px |
| 24 px | 左侧绘图条和独立入口 | 1.75 px | 2 px |

- 按原 SVG 24／28 网格等比缩进并居中，四边留白从描边外沿量起。保留点光标、拖点等既有填充和原有轮廓。
- 承接 v2 外壳色。默认 MUTED #9BA7B4，悬停 TEXT #E6EDF3，激活 ACCENT #1F5EBC，禁用用 MUTED 的 45% 不透明度。样张底为 APP_BG #0D1117，悬停示意底为 CONTROL_BG #1F2630。按钮交互面积沿用 v2。图表工具条最终状态色以图表增补审定值为准。
- 图标盒与相邻文字行盒垂直中心对齐。同行同档，按钮中水平居中。文字保持自身基线，间距 4 px，Tk 按显示比例栅格化。
- 资产名用小写 kebab-case，按用途区分 `drawing-arrow` 与 `arrow-right`、`zoom` 与 `plus`、普通搜索与 `quick-search`。星标和图格徽标用同一个轮廓承接各自状态。布局共用一个语义入口，样张用既有 2×2 代表，实际行列由原参数生成。
- 图表工具优先沿用原型 SVG，成长空间专用项沿用原 SVG，原生独有图标复用已存在的 Pillow 坐标。个别现有字形直接复用。品牌 ICO 保持原有多尺寸与品牌色。新增造型 0 个。
