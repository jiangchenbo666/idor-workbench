"""应用启动配置。

这里只读取环境变量并转换成有类型的不可变对象，不掺入业务规则。视图层和领域层
通过同一个 ``Settings`` 快照读取并发数、上传上限和 TLS 策略，避免各模块散落默认值。
环境变量在进程启动后变化不会自动生效，需要重启服务，这是有意设计。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    """读取正整数环境变量；非法或小于 1 的值回退到安全默认值。"""
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _boolean(name: str, default: bool) -> bool:
    """把常见部署写法（1/true/yes/on）统一解析为布尔值。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """进程级配置快照；``frozen=True`` 防止请求处理中被意外改写。"""

    task_workers: int
    ai_task_workers: int
    cors_origins: list[str]
    max_upload_bytes: int
    ai_verify_tls: bool


def get_settings() -> Settings:
    """从环境变量构建配置，并集中声明每项配置的默认值。"""
    origins = os.environ.get("IDOR_CORS_ORIGINS", "http://localhost:8001,http://127.0.0.1:8001")
    return Settings(
        task_workers=_positive_int("IDOR_TASK_WORKERS", 2),
        ai_task_workers=_positive_int("IDOR_AI_TASK_WORKERS", 1),
        cors_origins=[origin.strip() for origin in origins.split(",") if origin.strip()],
        max_upload_bytes=_positive_int("IDOR_MAX_UPLOAD_BYTES", 50 * 1024 * 1024),
        ai_verify_tls=_boolean("IDOR_AI_VERIFY_TLS", True),
    )
