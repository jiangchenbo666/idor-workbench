"""L2 composition boundary for the IDOR domain.

Views import application-facing dependencies here instead of reaching into L1.
This keeps the transport layer dependent on the domain only.
"""
from idor_workbench.foundation.database import WorkbenchDB
from idor_workbench.foundation.paths import PROJECTS_DIR, ROOT_DIR, STATIC_DIR
from idor_workbench.foundation.settings import Settings, get_settings

__all__ = ["PROJECTS_DIR", "ROOT_DIR", "STATIC_DIR", "Settings", "WorkbenchDB", "get_settings"]
