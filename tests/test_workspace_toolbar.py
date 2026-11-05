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
    from gui.workspace_toolbar import FAVORITES
    from workbench.store import Store

    class Host:
        def __init__(self,index):
            self.token=100+index
            self.calls=[]
            self.loaded=True
            self._last_workspace_state=dict(token=self.token,identity=f'synthetic-{index}',
                symbol=f'sh.60000{index}',timeframe='daily',asof='2026-11-04',tool='cross',
                available=['daily','weekly','monthly']+(['m5','m60','m240'] if index else []),
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
        assert [b.cget('text') for b in controls.period_buttons.values()]==['5分','1小时','天','周','月']
        for count in (1,4,9):
            grid.set_layout(count)
            for index,slot in enumerate(grid._slots):
                slot._embedded=Host(index)
                slot._title_var.set(f'sh.60000{index} 合成标的 · 历史日K')
            root.update()
            tools=controls.drawing_tools
            tools.refresh();tools.place_favorites()
            assert len(FAVORITES)==11
            assert sum(len(v) for k,v in tools.buttons.items() if k in dict(FAVORITES))>=11
            assert tools.favorites.winfo_ismapped()
            assert tools.favorites.winfo_manager()==('place' if count==1 else 'pack')
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
        for tool, _ in FAVORITES:
            tools.toggle_favorite(tool)
        controls.save()
        grid.destroy()
        grid=ChartGrid(root,store,interactive=True)
        restored=grid.controls.drawing_tools
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
