"""Commands shared by source and installed desktop subprocesses."""
import sys


def chart_command(session):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--chart', str(session)]
    return [sys.executable, '-m', 'gui.chart_shell', str(session)]
