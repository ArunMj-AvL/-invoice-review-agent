"""Pick the rules engine.

Normally this is Niranjan's ``app.rules``. If that module has not been merged yet,
``app.rules_reference`` (a contract-compliant stand-in) is used and a warning is logged,
so the graph and its tests are never blocked.
"""

import importlib
import logging
from types import ModuleType

logger = logging.getLogger(__name__)


def get_rules() -> ModuleType:
    try:
        return importlib.import_module("app.rules")
    except ModuleNotFoundError as exc:
        if exc.name != "app.rules":
            raise  # rules.py exists but one of ITS imports is broken: surface that
        logger.warning("app/rules.py not found - using app/rules_reference.py stand-in")
        return importlib.import_module("app.rules_reference")
