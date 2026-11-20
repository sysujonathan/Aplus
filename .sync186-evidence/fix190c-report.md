# fix190c 报告：更新日志与离线目录同源（CI run 44657318322 唯一失败项）

## 失败项
tests/test_release_versions.py::test_generated_document_and_offline_catalog_are_one_source
（CI 2914 项中唯一失败；main 47fd109 上通过，本 PR 引入。）

## 根因
ef64618 往 docs/更新日志.md 手写新增「## 桌面同步 · 待发行」一节，但单一来源 release_catalog.json 里没有对应条目，
生成器 changelog_text() 渲染结果不含这一节，文档与目录对不上。

## 修法
- release_catalog.json：新增顶层 "unreleased" 数组，收录该节（标题＋两条说明，原样保留）。
- workbench/releases.py：parse_catalog() 校验 unreleased 结构；changelog_text() 在历史版本前渲染 unreleased 各节。
- docs/更新日志.md：由生成器重出（python: changelog_text(read_catalog(ROOT))），不手改正文。
- 注：10:53 轮自查曾先行推送 ba2824f（直接删节）。本提交在其之上，把被删内容改由目录生成恢复，文档内容与删节前一致。

## 验收（Lord 本机，工作树 Aplus-wt-sync186b）
- tests/test_release_versions.py：34 passed, 1 skipped。
- tests/test_store_update_compatibility.py：47 passed（fix190b 回归不受影响）。
- git diff 相对 main：仅 release_catalog.json、workbench/releases.py、docs/更新日志.md 三处；core/strategies/frozen_manifest.json 无改动。
- 性能影响：仅文档与离线目录数据，不碰运行时热路径。

## 提交
分支 codex/sync-186-delivery，新头待推送。
