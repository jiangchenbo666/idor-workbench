"""Enforce the repository's L1/L2/L3 import rules without network dependencies.

The checker intentionally resolves local imports instead of trusting package
prefixes. A root-level module such as ``workbench_runner`` must not become a
back door around the package architecture.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "idor_workbench"
IGNORED_LOCAL_DIRS = {".git", ".venv", ".runtime", ".pytest_cache", ".playwright-mcp", "__pycache__", "data"}
FORBIDDEN_LEGACY_MODULES = {
    "ai_assistant",
    "assertion_engine",
    "async_privilege_test",
    "frontend_probe",
    "generate_report",
    "scenario_engine",
    "workbench_runner",
}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    reason: str
    fix: str


def layer_for(path: Path) -> tuple[str, str | None]:
    relative = path.relative_to(PACKAGE)
    if relative.parts[0] == "foundation":
        return "L1", None
    if relative.parts[0] == "views":
        return "L3", None
    if relative.parts[0] == "domains":
        return "L2", relative.parts[1] if len(relative.parts) > 1 else None
    return "other", None


def local_source_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*.py"):
        relative_parts = path.relative_to(ROOT).parts
        if relative_parts and relative_parts[0] in IGNORED_LOCAL_DIRS:
            continue
        files.append(path)
    return files


def local_root_modules() -> set[str]:
    return {path.stem for path in ROOT.glob("*.py")}


def imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend((node.lineno, item.name) for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append((node.lineno, node.module))
    return result


def imported_local_target(module: str) -> tuple[str, str | None] | None:
    """Return (layer, domain) for local imports; None means stdlib/third-party."""
    top_level = module.split(".")[0]
    if top_level in FORBIDDEN_LEGACY_MODULES:
        return "root", None
    if top_level in local_root_modules():
        return "root", None
    if top_level == "webapp":
        return "webapp", None
    if top_level != "idor_workbench":
        return None

    parts = module.split(".")
    if len(parts) == 1:
        return "package-root", None
    if parts[1] == "foundation":
        return "L1", None
    if parts[1] == "views":
        return "L3", None
    if parts[1] == "domains":
        return "L2", parts[2] if len(parts) > 2 else None
    return "package-other", None


def check_file(path: Path) -> list[Violation]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    layer, domain = layer_for(path)
    violations: list[Violation] = []
    for line, module in imported_modules(tree):
        imported = imported_local_target(module)
        if imported is None:
            continue
        imported_layer, imported_domain = imported
        if imported_layer == "root":
            violations.append(Violation(
                path,
                line,
                "导入了根目录本地模块，绕过了 L1/L2/L3 架构边界",
                "删除根目录兼容模块；把引用改为 idor_workbench.foundation / idor_workbench.domains / idor_workbench.views 下的标准路径。",
            ))
        elif imported_layer == "webapp":
            violations.append(Violation(
                path,
                line,
                "导入了 webapp 旧入口/旁路模块",
                "后端入口统一为 idor_workbench.views.api:app；共享能力放入 L1，业务能力放入 L2。",
            ))
        elif layer == "L1" and imported_layer in {"L2", "L3"}:
            violations.append(Violation(
                path,
                line,
                "L1 基础层反向依赖了领域层或视图层",
                "将共享逻辑下沉到 L1，或由 L2/L3 调用 L1；L1 不得感知业务或 HTTP 视图。",
            ))
        elif layer == "L2" and imported_layer == "L3":
            violations.append(Violation(
                path,
                line,
                "L2 领域层反向依赖了 L3 视图层",
                "把 HTTP/页面适配逻辑移到 L3，并通过参数把数据传入领域服务。",
            ))
        elif layer == "L2" and imported_layer == "L2":
            if domain and imported_domain and imported_domain != domain:
                violations.append(Violation(
                    path,
                    line,
                    "L2 领域之间直接依赖",
                    "提取共享代码到 L1，或在 L3 组合两个领域；同层领域不得互相引用。",
                ))
        elif layer == "L3" and imported_layer == "L1":
            violations.append(Violation(
                path,
                line,
                "L3 视图层绕过 L2 直接依赖基础层",
                "通过对应领域的 application/service 接口访问该能力；L3 只编排 L2，不直连 L1。",
            ))
    return violations


def main() -> int:
    violations: list[Violation] = []
    for path in local_source_files():
        if path.is_relative_to(PACKAGE):
            violations.extend(check_file(path))
    if not violations:
        print("Architecture check passed: L3 -> L2 -> L1 dependency direction is intact; root-module bypasses are blocked.")
        return 0
    for item in violations:
        print(f"{item.path.relative_to(ROOT)}:{item.line}: ERROR architecture: {item.reason}. Fix: {item.fix}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
