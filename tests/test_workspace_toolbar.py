"""Shared native controls retain real renderer state and token ownership."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


@pytest.mark.native_process
def test_native_shared_icons_menus_favorites_and_token_state(tmp_path):
    import tkinter as tk
    import ttkbootstrap as ttk
    from gui.chart_panel import ChartGrid
    from gui.workspace_toolbar import FAVORITES, WorkspaceQuickSearch
    from workbench.store import Store
    from workbench.workspace_layouts import new_layout

    class Host:
        def __init__(self,index):
            self.token=100+index
            self.calls=[]
            self.loaded=True
            self._last_workspace_state=dict(token=self.token,identity=f'synthetic-{index}',
                symbol=f'sh.60000{index}',timeframe='daily',asof='2026-11-04',tool='cross',
                available=['daily','weekly','monthly']+(['m1','m5','m15','m30','m60'] if index else []),
                magnet=True,locked=False,hidden=False)
        def ready(self):return self.loaded
        def command(self,kind,value=None):
            self.calls.append((kind,value));return self.loaded

    store=Store(tmp_path)
    root=ttk.Window(themename='darkly')
    root.geometry('1600x1000')
    grid=ChartGrid(root,store,interactive=True)
    grid.pack(fill=tk.BOTH,expand=True)
    try:
        controls=grid.controls
        root.update()
        controls.layout_button.invoke()
        assert controls._layout_popup is not None
        controls.layout_button.event_generate('<ButtonPress-1>')
        controls.layout_button.event_generate('<ButtonRelease-1>')
        root.update()
        assert controls._layout_popup is None
        controls.layout_button.invoke()
        assert set(controls._layout_popup.choices)=={1,4,6,8,9}
        controls._layout_popup.choices[8].invoke()
        assert grid._shape==(2,4) and controls._layout_popup is None
        strip = controls.period_more.master
        assert [b.cget('text') for b in strip.pack_slaves()]==['5分','1小时','天','周','月','⌄']
        controls.period_more.invoke()
        picker = controls._period_popup
        assert list(picker.choices) == ['m1','m5','m15','m30','m60','daily','weekly','monthly']
        picker.stars['m30'].invoke()
        picker.stars['m1'].invoke()
        assert picker.stars['m1'].cget('text') == '★'
        assert [b.cget('text') for b in strip.pack_slaves()]==['1分','5分','30分','1小时','天','周','月','⌄']
        assert store.load_ui_preferences('chart_periods')['data']['favorites'] == [
            'm1','m5','m30','m60','daily','weekly','monthly']
        picker.stars['m1'].invoke()
        picker.stars['m30'].invoke()
        controls._close_period_picker()
        for count in (1,4,9):
            grid.set_layout(count)
            for index,slot in enumerate(grid._slots):
                slot._embedded=Host(index)
                slot._title_var.set(f'sh.60000{index} 合成标的 · 历史日K')
            root.update()
            tools=controls.drawing_tools
            tools.refresh();tools.place_favorites()
            if count==1:
                def choose_settings():
                    dialog = next(child for child in root.winfo_children()
                                  if isinstance(child,WorkspaceQuickSearch))
                    dialog.query.set('图表设置')
                    dialog.ok()
                root.after(30,choose_settings)
                controls.quick_search_button.invoke()
                root.update()
                assert grid._slots[0]._embedded.calls[-1]==('action','settings')
            assert len(FAVORITES)==11
            assert sum(len(v) for k,v in tools.buttons.items() if k in dict(FAVORITES))>=11
            assert tools.favorites.winfo_ismapped()
            assert tools.favorites.winfo_manager()=='place'
            assert tools.favorites.place_info()['in'] is grid.host
            assert all(button.winfo_ismapped() for button in (controls.symbol,controls.compare_button,controls.layout_button))
            assert tools.buttons['trend'][-1].cget('image')
            assert controls.period_buttons['m5'].cget('state')=='disabled'
            controls.period_buttons['weekly'].invoke()
            assert grid._slots[0]._embedded.calls[-1]==('timeframe','weekly')
            tools.buttons['trend'][-1].invoke()
            assert grid._slots[0]._embedded.calls[-1]==('tool','trend')
            if count>1:
                old=grid._slots[0]._embedded
                old._last_workspace_state.update(locked=True,tool='ray')
                tools.refresh()
                assert tools.buttons['lock'][0].cget('background')=='#ECECEC'
                grid._activate_slot(1)
                tools.refresh()
                assert tools.buttons['lock'][0].cget('background')!='#ECECEC'
                tools.buttons['fib'][-1].invoke()
                assert grid._slots[1]._embedded.calls[-1]==('tool','fib')
                assert old.calls[-1]==('active',False)
                assert '600001' in controls.target.get()
                assert controls.period_buttons['m5'].cget('state')=='normal'
                before=dict(controls.states)
                controls.state_changed(1,dict(token=99,timeframe='weekly',dirty=True))
                assert controls.states==before
            tools.menu('fib');root.update()
            assert tools.popup and tools.popup.winfo_exists()
            entries=tools.popup._entries
            assert entries[0][0]=='斐波那契回撤' and entries[0][1]=='fib'
            assert all(button.cget('state')=='disabled' for _,action,button in entries if action is None)
            tools.arrows['fib'].event_generate('<ButtonPress-1>')
            tools.arrows['fib'].event_generate('<ButtonRelease-1>')
            root.update()
            assert tools.popup is None
            tools.arrows['smile'].invoke()
            name, action, button = tools.popup._entries[0]
            assert (name,action,button.cget('state'))==('图标','smile','normal')
            button.invoke()
            assert grid._slots[grid._active_slot]._embedded.calls[-1]==('tool','smile')
            # An implemented command is disabled during loading, not marked unimplemented.
            host=grid._slots[grid._active_slot]._embedded
            host.loaded=False;tools.refresh()
            assert tools.buttons['trend'][-1].cget('state')=='disabled'
            assert controls.symbol.cget('state')=='normal'
            host.loaded=True;tools.refresh()
            tools.close_menu()
            for slot in grid._slots:slot._embedded=None
            grid._active_slot=0
        tools.toggle_favorites()
        assert not tools.favorites.winfo_ismapped()
        controls.save()
        assert store.load_ui_preferences('chart_layouts')['data']['toolbar']['favoritesVisible'] is False
        tools.toggle_favorites();root.update()
        assert tools.favorites.winfo_ismapped()
        controls._toggle_favorite_layout()
        first = controls.current['id']
        six = new_layout('六股T+0',2,3)
        controls.layouts.append(six)
        controls.open_layout(six['id'])
        controls._toggle_favorite_layout()
        assert [b.cget('text') for b in controls.layout_shortcuts.winfo_children()]==['默','六']
        controls.layout_shortcuts.winfo_children()[0].invoke()
        assert controls.current['id']==first
        controls.layout_shortcuts.winfo_children()[1].invoke()
        assert controls.current['id']==six['id'] and grid._shape==(2,3)
        root.update()
        tools.toggle_favorite('text')
        root.update()
        handle = tools.favorite_handle
        x, y = handle.winfo_rootx()+3, handle.winfo_rooty()+3
        before = tools.favorites.winfo_rootx(), tools.favorites.winfo_rooty()
        handle.event_generate('<ButtonPress-1>',rootx=x,rooty=y)
        handle.event_generate('<B1-Motion>',rootx=x+80,rooty=y+60,state=256)
        handle.event_generate('<ButtonRelease-1>',rootx=x+80,rooty=y+60)
        root.update()
        assert tools.favorites.winfo_rootx()>before[0] and tools.favorites.winfo_rooty()>before[1]
        saved_position = tools.position
        assert tools.favorite_buttons['text'].winfo_ismapped()
        controls.save()
        assert store.load_ui_preferences('chart_layouts')['data']['toolbar']['favorites']==sorted(tools.favorite_selection)
        narrow = max(controls.left_actions.winfo_reqwidth(),controls.right.winfo_reqwidth())+20
        root.geometry(f'{narrow}x760')
        root.update()
        assert controls.right.grid_info()['row']==1
        assert controls.period_buttons['weekly'].winfo_ismapped() and controls.snapshot_button.winfo_ismapped()
        for tool, _ in FAVORITES:
            tools.toggle_favorite(tool)
        tools.toggle_favorite('text')
        controls.save()
        grid.destroy()
        grid=ChartGrid(root,store,interactive=True)
        restored=grid.controls.drawing_tools
        assert grid.controls.favorite_layouts==[first,six['id']]
        assert restored.position==saved_position
        assert not restored.favorite_selection
        assert not any(button.master is restored.favorites and button.winfo_manager()
                       for buttons in restored.buttons.values() for button in buttons)
        grid.pack(fill=tk.BOTH,expand=True)
        root.update()
        assert not restored.favorites.winfo_ismapped()
        restored.toggle_favorite('trend')
        root.update()
        assert restored.favorites.winfo_ismapped()
        assert restored.buttons['trend'][-1].winfo_ismapped()
    finally:
        for slot in grid._slot_pool:slot._embedded=None
        root.destroy()


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows Tk and WebView2 child windows')
def test_real_shared_renderer_workflow(tmp_path):
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,str(root/'tests/workspace_toolbar_probe.py'),str(tmp_path)],
        cwd=root,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=240)
    assert result.returncode==0,result.stdout+result.stderr
