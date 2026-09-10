"""统一计算工作区目录，防止各模块用不同相对路径读写同一份产物。"""
from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
STATIC_DIR = ROOT_DIR / "webapp" / "static"


def project_dir(project_id: str) -> Path:
    """返回项目产物目录；调用前必须已在视图层校验 ``project_id``。"""
    return PROJECTS_DIR / project_id
