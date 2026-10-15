"""Test-only handling of one narrowly identified Windows Tcl startup flake."""
import os
import re
import warnings
from gc import collect
from pathlib import Path
from time import sleep

from tkinter import TclError


WINDOWS = os.name == 'nt'


def _transient_init_read(error):
    message = str(error)
    if not WINDOWS or not message.startswith("Can't find a usable init.tcl"):
        return False
    match = re.search(r'couldn\'t read file "([^"\r\n]+[/\\]init\.tcl)": No error(?:\r?\n|$)', message)
    if not match:
        return False
    try:
        # A missing/broken installation is not a transient read. Do not change
        # TCL_LIBRARY or install/copy files to make an invalid environment pass.
        return bool(Path(match[1]).read_bytes())
    except OSError:
        return False


def create_window(factory):
    """Retry setup once, never a test body, assertion or general Tk error.

    The same error is reported intermittently on Windows hosted runners:
    https://github.com/actions/setup-python/issues/1102
    A second failure is propagated unchanged; no test is skipped.
    """
    try:
        return factory()
    except TclError as error:
        if not _transient_init_read(error):
            raise
        warnings.warn('Windows Tcl init.tcl was readable but initialization failed; '
                      'retrying native window setup once', RuntimeWarning, stacklevel=2)
    collect()
    sleep(.1)
    return factory()
