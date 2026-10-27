"""Barloom is Quantarium's name before 1.0; this package keeps old imports working.

``import barloom.model`` gives the very module ``quantarium.model``, so
strategy scripts and saved entrypoints written for Barloom run unchanged.
New code imports ``quantarium``.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

import quantarium

__version__ = quantarium.__version__


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Finds ``barloom.X`` as ``quantarium.X``, the same module object under both names."""

    def find_spec(self, name, path=None, target=None):
        # barloom.__main__ is a real file, so ``python -m barloom`` keeps working.
        if name.startswith(__name__ + ".") and name != __name__ + ".__main__":
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        return importlib.import_module("quantarium" + spec.name[len(__name__):])

    def exec_module(self, module):
        # The module was executed once, as quantarium.X; running it again would duplicate its state.
        pass


if not any(isinstance(finder, _Alias) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Alias())
