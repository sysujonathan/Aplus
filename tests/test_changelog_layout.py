"""Native layout checks use an isolated window/catalog, never the running app."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows native log view')


def native(script, *args):
    result = subprocess.run([sys.executable, '-c', script, *map(str, args)], cwd=ROOT,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_log_has_font_hierarchy_bullets_rules_and_no_markdown():
    native(r'''
import copy
import tkinter as tk
from tkinter.font import Font
import ttkbootstrap as ttk
from gui.usage_help import show_usage, render_changelog
from workbench.releases import read_catalog
w=ttk.Window(themename='darkly'); w.withdraw()
dialog=show_usage(w,tab='更新日志'); dialog.update()
tabs=dialog._help_tabs
page=dialog.nametowidget(tabs.select())
text=next(x for x in page.winfo_children() if isinstance(x,tk.Text))
body=text.get('1.0','end')
assert '##' not in body and '[对应PR]' not in body and 'https://' not in body
assert 'v2026.10.11.35' in body and 'PR35  ·  预览版 · 待合入主线' in body
assert 'v2026.10.11.34' in body and 'PR34  ·  预览版 · 待合入主线' in body
assert 'v2026.10.11.32' in body and 'PR32  ·  已合入主线' in body
assert 'v2026.10.11.31' in body and 'PR31  ·  已合入主线' in body
assert 'PR23' in body and '随 PR22 纳入主线' in body
assert '流程测试（无发布编号）' in body and '未发布' in body
catalog=read_catalog('.')
assert body.count('•  ')==sum(len(row['notes']) for row in catalog['history'])
assert len(text.tag_ranges('version'))==2*len(catalog['history'])
assert len(text._release_rules)==len(catalog['history'])-1
version=Font(w,font=text.tag_cget('version','font'))
title=Font(w,font=text.tag_cget('log_title','font'))
metadata=Font(w,font=text.tag_cget('metadata','font'))
base=Font(w,font=text.cget('font'))
assert title.cget('size')>version.cget('size')>base.cget('size')>metadata.cget('size')
assert version.cget('weight')==title.cget('weight')=='bold'
assert text.tag_cget('metadata','foreground')!=text.cget('foreground')
assert text.winfo_pixels(text.tag_cget('bullet','lmargin2'))>text.winfo_pixels(text.tag_cget('bullet','lmargin1'))
assert str(text.cget('state'))=='disabled'
text.insert('end','unauthorized edit'); assert text.get('1.0','end')==body
# Re-render does not mutate facts or leave duplicate rules/windows.
before=copy.deepcopy(catalog); old=list(text._release_rules)
render_changelog(text,catalog); dialog.update()
assert catalog==before and all(not x.winfo_exists() for x in old)
assert len(text.window_names())==len(catalog['history'])-1
assert show_usage(w,tab='更新日志') is dialog
w.destroy()
''')


@pytest.mark.parametrize('scaling', [1.25, 2.0])
def test_rules_follow_viewport_and_long_bullets_wrap(scaling):
    native(r'''
import sys
import tkinter as tk
import ttkbootstrap as ttk
from gui.usage_help import show_usage, render_changelog
from workbench.releases import read_catalog
w=ttk.Window(themename='darkly'); w.withdraw(); w.tk.call('tk','scaling',float(sys.argv[1]))
dialog=show_usage(w,tab='更新日志'); tabs=dialog._help_tabs
page=dialog.nametowidget(tabs.select())
text=next(x for x in page.winfo_children() if isinstance(x,tk.Text))
c=read_catalog('.'); c['history'][-1]['notes'].append('长中文说明用于核验自动换行。'*35)
render_changelog(text,c)
previous=0
for width in (620,820,1200):
    dialog.geometry(f'{width}x680'); dialog.update()
    rule=text._release_rules[0]
    # Off-screen embedded widgets are unmapped (winfo_width may be 1).
    # Verify the requested dimensions for every rule, including hidden ones.
    rule_width=int(rule.cget('width'))
    assert previous<rule_width<text.winfo_width()
    assert all(int(x.cget('width'))==rule_width for x in text._release_rules)
    assert int(rule.winfo_children()[0].cget('height'))==1
    previous=rule_width
    start=text.search('长中文说明','1.0')
    assert text.count(start,start+' lineend','displaylines')[0]>1
    assert int(text.cget('wrap')=='word') and str(text.cget('state'))=='disabled'
    assert text.yview()[1]<1.0
    text.yview_moveto(1); dialog.update()
    # Tk estimates total height after resizing mixed-font/embedded lines.
    # The actual last line must be visible, regardless of that estimate.
    end_line=text.dlineinfo('end-1c')
    assert end_line is not None and end_line[1]+end_line[3]<text.winfo_height()
    text.yview_moveto(0); dialog.update()
# Mouse wheel on an embedded divider still scrolls the text.
rule=text._release_rules[0]
text.see(str(rule)); dialog.update()
before=text.yview()[0]
rule.event_generate('<MouseWheel>',delta=-120); dialog.update()
assert text.yview()[0]>before
w.destroy()
''', scaling)


def test_missing_catalog_keeps_readable_read_only_message():
    native(r'''
import tkinter as tk
import ttkbootstrap as ttk
from gui.usage_help import render_changelog
w=ttk.Window(themename='darkly'); w.withdraw()
t=tk.Text(w); t.pack(); render_changelog(t,None); w.update()
assert '更新日志暂未安装' in t.get('1.0','end')
assert not t._release_rules and str(t.cget('state'))=='disabled'
w.destroy()
''')
