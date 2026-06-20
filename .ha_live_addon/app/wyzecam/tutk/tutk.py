"""TUTK FFI interface -- backward-compatibility re-export facade.

The implementation has been split into three themed modules:

* :mod:`tutk_structures` -- ctypes ``Structure`` subclasses and type defs.
* :mod:`tutk_core` -- constants, :class:`TutkError`, and :func:`load_library`.
* :mod:`tutk_ffi` -- the ctypes function bindings into ``libIOTCAPIs_ALL``.

This module re-exports every public name from those modules so that existing
callers (``from wyzecam.tutk import tutk`` then ``tutk.TutkError`` etc.)
continue to work without modification.
"""
from .tutk_core import *  # noqa: F401,F403
from .tutk_core import __all__ as _core_all
from .tutk_ffi import *  # noqa: F401,F403
from .tutk_ffi import __all__ as _ffi_all
from .tutk_structures import *  # noqa: F401,F403
from .tutk_structures import __all__ as _structures_all

__all__ = [*_core_all, *_ffi_all, *_structures_all]
