"""回归：流水线缓存 key 必须包含快照输入（Issue #134）。

PR #128（界面缓存）与 PR #129（客户端读快照）合并后，快照输入没有进
`MatchList.load_from_store` 的 `_pipeline_cache` key：走快照轨时，服务
端换了新快照文件、而本地库/回执/引擎指纹都不变，界面会命中旧缓存、
继续显示上一份候选（静默旧数据）。`gui/data.py::snapshot_inputs()` 的
行为本体已由 test_premarket_snapshot 的专门用例覆盖；本用例只钉住
“key 组成确实调用了它”这条联动，防止重构时再次静默丢失。
"""
import inspect

from gui import match_list


def test_pipeline_cache_key_includes_snapshot_inputs():
    source = inspect.getsource(match_list.MatchList.load_from_store)
    # key 元组从 "key = (" 起、到缓存查表 "cached = getattr" 止，
    # snapshot_inputs() 必须出现在这段之内（即确实进了 key 组成）。
    key_block = source.split("key = (", 1)[1].split("cached = getattr", 1)[0]
    assert "snapshot_inputs()" in key_block
