"""Execute exactly the protected source bytes, never a stale/tampered pyc."""
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys


class VerifiedSourceLoader(importlib.machinery.SourceFileLoader):
    def __init__(self,name,path,expected):
        super().__init__(name,str(path));self.expected=expected

    def get_code(self,fullname):
        source=Path(self.path).read_bytes()
        if hashlib.sha256(source).hexdigest()!=self.expected:
            raise ImportError('冻结源码校验失败，已停止：'+fullname)
        # Compile the very bytes just verified. SourceFileLoader's normal pyc
        # cache shortcut must not substitute another unverified code object.
        return compile(source,self.path,'exec',dont_inherit=True)


class FrozenSourceFinder(importlib.abc.MetaPathFinder):
    def __init__(self,root):
        self.root=Path(root).resolve()
        manifest=json.loads((self.root/'frozen_manifest.json').read_text(encoding='utf-8'))
        self.expected={row['path']:row['sha256'] for row in manifest['files']}

    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] not in ('core','config'):return None
        relative=fullname.replace('.','/')
        package=relative+'/__init__.py'
        key=package if package in self.expected else relative+'.py'
        if key not in self.expected:raise ImportError('冻结模块不在发布清单中：'+fullname)
        source=self.root/key
        loader=VerifiedSourceLoader(fullname,source,self.expected[key])
        return importlib.util.spec_from_file_location(fullname,source,loader=loader,
            submodule_search_locations=[str(source.parent)] if key==package else None)


def install():
    root=Path(__file__).resolve().parents[1]
    sys.meta_path.insert(0,FrozenSourceFinder(root))
