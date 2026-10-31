"""Frozen desktop entry; chart processes never open the first-run wizard."""
import json
import os
from pathlib import Path
import sys
import traceback


def main():
    sys.dont_write_bytecode=True  # Installed payloads stay immutable between releases.
    from workbench.installed_imports import install
    install()
    if len(sys.argv) == 3 and sys.argv[1] == '--provider-worker':
        from workbench.process_entry import provider_command, restore_worker_stdio
        provider_command(sys.argv[2])  # Validate before restoring private IPC.
        restore_worker_stdio()
        from workbench.provider_worker import main as provider
        return provider(sys.argv[2])
    if len(sys.argv) > 1 and sys.argv[1] == '--chart':
        from gui.chart_shell import main as chart
        return chart(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == '--growth':
        from gui.growth_shell import main as growth
        return growth(sys.argv[2:])
    if len(sys.argv) > 2 and sys.argv[1] == '--self-test':
        from workbench.strategies import verify_frozen, catalog
        from gui.chart_shell import ASSETS, webview_available
        from workbench.acceleration_profile import ReadOnlyState
        specs=catalog(ReadOnlyState())
        paths=[Path(item.file).resolve() for item in specs.values()]
        # Entry scripts live at the bundle root. Actual detector classes must
        # originate from protected on-disk sources, not another PYZ copy.
        from workbench.store import ROOT
        if any(path.suffix!='.py' or not path.is_file() or ROOT/'core' not in path.parents for path in paths):
            raise RuntimeError('实际策略模块与冻结源码位置不一致')
        loaders=[type(sys.modules[item.cls.__module__].__loader__).__name__ for item in specs.values()]
        if any(name!='VerifiedSourceLoader' for name in loaders):
            raise RuntimeError('冻结策略未通过已校验源码加载器执行')
        report = dict(frozen=verify_frozen(), strategies=len(specs),
                      actual_strategy_files=[str(path) for path in paths],
                      actual_strategy_loaders=loaders,
                      chart_assets=(ASSETS/'index.html').exists(), webview=webview_available(),
                      growth_assets=all((ROOT/'gui'/'pa_growth'/name).is_file()
                                        for name in ('index.html','growth.css','growth.js')),
                      executable=sys.executable)
        Path(sys.argv[2]).write_text(json.dumps(report), encoding='utf-8')
        if not report['chart_assets'] or not report['webview'] or not report['growth_assets']:
            raise RuntimeError('安装包图表或成长空间组件不完整')
        return
    if len(sys.argv) > 2 and sys.argv[1] == '--installed-update':
        from workbench.installed_update import worker
        return worker(Path(sys.argv[2]))
    if len(sys.argv) > 2 and sys.argv[1] == '--smoke-test':
        from tools.installer_smoke import run
        return run(sys.argv[2])
    if len(sys.argv) > 1:
        raise ValueError('不支持的 Aplus 启动参数，未打开重复桌面')
    from workbench.installed_runtime import launch
    launch()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'Aplus'/'logs'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/'installed-startup.log'
        path.write_text(traceback.format_exc(), encoding='utf-8')
        if len(sys.argv) == 1:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, 'Aplus 未能启动。原数据已保留。\n诊断位置：'+str(path), 'Aplus', 0x10)
        raise SystemExit(1)
