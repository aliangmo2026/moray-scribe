"""Sample import forms."""

from __future__ import annotations

import os
import os.path as osp
from collections import OrderedDict
from collections.abc import Mapping as ABCMapping

from . import service
from .core import Engine


def touch() -> tuple[object, ...]:
    """Reference every imported binding so the module stays lint-clean."""
    return (
        os.sep,
        osp.join("a", "b"),
        OrderedDict(),
        ABCMapping,
        Engine,
        service.Service,
    )
