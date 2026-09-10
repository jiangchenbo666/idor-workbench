# L1 shared SQLite repository.
"""IDOR Workbench 的 SQLite 元数据存储层。

SQLite 只保存方便检索的元数据和任务状态。
HAR、截图、run_results.json、report.docx 这类大文件仍保存在 data/projects/<project_id>/。
这样适合当前小团队部署：有数据库能力，但不把所有大文件都硬塞进 SQL。
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


def utcnow_text() -> str:
    """生成数据库展示时间；当前项目统一使用服务器本地时区。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class WorkbenchDB:
    """L3 API 使用的小型数据仓库对象。

    其他后端代码应该调用这里的方法，而不是在路由函数里散落 SQL。
    """
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield one short-lived connection and always close it.

        ``sqlite3.Connection`` used as a context manager commits or rolls
        back, but it does *not* close itself. Keeping the lifecycle here
        prevents request-heavy test/execution flows from leaking file handles
        and holding a Windows database file open during cleanup.
        """
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """服务启动时建表；使用 IF NOT EXISTS，所以重复执行也是安全的。"""
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                  project_id TEXT PRIMARY KEY,
                  project_name TEXT NOT NULL,
                  base_url TEXT,
                  model_type TEXT,
                  owner_user_id TEXT,
                  project_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_runs (
                  task_id TEXT PRIMARY KEY,
                  project_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  status TEXT NOT NULL,
                  message TEXT,
                  result_json TEXT,
                  error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS run_history (
                  run_id TEXT PRIMARY KEY,
                  project_id TEXT NOT NULL,
                  run_time TEXT,
                  env TEXT,
                  returncode INTEGER,
                  total INTEGER,
                  passed INTEGER,
                  failed INTEGER,
                  skipped INTEGER,
                  meta_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS source_files (
                  source_id TEXT PRIMARY KEY,
                  project_id TEXT,
                  owner_user_id TEXT,
                  kind TEXT NOT NULL,
                  original_name TEXT,
                  stored_path TEXT NOT NULL,
                  size INTEGER,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS users (
                  user_id TEXT PRIMARY KEY,
                  username TEXT NOT NULL UNIQUE,
                  password_hash TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_sessions (
                  token TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_ai_settings (
                  user_id TEXT PRIMARY KEY,
                  settings_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            if "owner_user_id" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN owner_user_id TEXT")
            source_columns = {row[1] for row in conn.execute("PRAGMA table_info(source_files)").fetchall()}
            if "owner_user_id" not in source_columns:
                conn.execute("ALTER TABLE source_files ADD COLUMN owner_user_id TEXT")

    def upsert_project(self, project: dict[str, Any]) -> None:
        """新增或更新项目的精简元数据，同时保存一份当前 project JSON 快照。"""
        now = utcnow_text()
        project_id = str(project.get("project_id") or "")
        with self.connect() as conn:
            existing = conn.execute("SELECT created_at FROM projects WHERE project_id=?", (project_id,)).fetchone()
            conn.execute(
                """
                INSERT INTO projects(project_id, project_name, base_url, model_type, owner_user_id, project_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                  project_name=excluded.project_name,
                  base_url=excluded.base_url,
                  model_type=excluded.model_type,
                  owner_user_id=COALESCE(projects.owner_user_id, excluded.owner_user_id),
                  project_json=excluded.project_json,
                  updated_at=excluded.updated_at
                """,
                (
                    project_id,
                    project.get("project_name") or project_id,
                    project.get("base_url") or "",
                    project.get("model_type") or "",
                    project.get("owner_user_id") or None,
                    json.dumps(project, ensure_ascii=False),
                    existing["created_at"] if existing else now,
                    now,
                ),
            )

    def list_projects(self, owner_user_id: str | None = None) -> list[dict[str, Any]]:
        """只列出指定所有者的项目；不传所有者时故意返回空列表而非全表。"""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM projects WHERE owner_user_id=? ORDER BY updated_at DESC", (owner_user_id,)).fetchall() if owner_user_id else []
        projects = []
        for row in rows:
            try:
                project = json.loads(row["project_json"] or "{}")
            except ValueError:
                project = {}
            project.setdefault("project_id", row["project_id"])
            project.setdefault("project_name", row["project_name"])
            project.setdefault("base_url", row["base_url"] or "")
            project.setdefault("model_type", row["model_type"] or "")
            project.setdefault("updated_at", row["updated_at"] or "")
            projects.append(project)
        return projects

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        """读取项目快照；是否有权读取必须由上层先用 ``project_owned_by`` 判断。"""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if not row:
            return None
        try:
            project = json.loads(row["project_json"] or "{}")
        except ValueError:
            project = {}
        project.setdefault("project_id", row["project_id"])
        project.setdefault("project_name", row["project_name"])
        project.setdefault("base_url", row["base_url"] or "")
        project.setdefault("model_type", row["model_type"] or "")
        project.setdefault("updated_at", row["updated_at"] or "")
        return project

    def project_owned_by(self, project_id: str, user_id: str) -> bool:
        """执行项目级授权判断，项目 ID 本身不能充当访问凭据。"""
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM projects WHERE project_id=? AND owner_user_id=?", (project_id, user_id)).fetchone()
        return bool(row)

    def delete_project(self, project_id: str) -> None:
        """删除项目元数据；外部目录清理由应用层在授权后完成。"""
        with self.connect() as conn:
            conn.execute("DELETE FROM projects WHERE project_id=?", (project_id,))

    def create_task(self, task: dict[str, Any]) -> None:
        """持久化排队/运行中的任务状态，让前端轮询在服务短暂重启后也更容易恢复。"""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO task_runs(task_id, project_id, kind, status, message, result_json, error, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.get("task_id"),
                    task.get("project_id"),
                    task.get("kind"),
                    task.get("status"),
                    task.get("message") or "",
                    json.dumps(task.get("result"), ensure_ascii=False) if task.get("result") is not None else "",
                    task.get("error") or "",
                    task.get("created_at") or utcnow_text(),
                    task.get("updated_at") or utcnow_text(),
                ),
            )

    def update_task(self, task: dict[str, Any]) -> None:
        """更新任务状态机快照，供前端轮询 queued/running/done/failed。"""
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE task_runs
                SET status=?, message=?, result_json=?, error=?, updated_at=?
                WHERE task_id=?
                """,
                (
                    task.get("status"),
                    task.get("message") or "",
                    json.dumps(task.get("result"), ensure_ascii=False) if task.get("result") is not None else "",
                    task.get("error") or "",
                    task.get("updated_at") or utcnow_text(),
                    task.get("task_id"),
                ),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """读取持久化任务；上层仍需按任务的 ``project_id`` 做所有权检查。"""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM task_runs WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return None
        result_text = row["result_json"] or ""
        return {
            "task_id": row["task_id"],
            "project_id": row["project_id"],
            "kind": row["kind"],
            "status": row["status"],
            "message": row["message"] or "",
            "result": json.loads(result_text) if result_text else None,
            "error": row["error"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def insert_run_history(self, project_id: str, meta: dict[str, Any]) -> None:
        """登记一次归档运行；详细结果仍保存在 runs/<run_id>/run_results.json。"""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO run_history(run_id, project_id, run_time, env, returncode, total, passed, failed, skipped, meta_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta.get("run_id"),
                    project_id,
                    meta.get("time") or "",
                    meta.get("env") or "",
                    meta.get("returncode"),
                    int(meta.get("total") or 0),
                    int(meta.get("passed") or 0),
                    int(meta.get("failed") or 0),
                    int(meta.get("skipped") or 0),
                    json.dumps(meta, ensure_ascii=False),
                    utcnow_text(),
                ),
            )

    def delete_run_history(self, run_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM run_history WHERE run_id=?", (run_id,))

    def upsert_source_file(self, source: dict[str, Any], project_id: str | None = None) -> None:
        """记录原始文件及其归属，避免上传物被其他账号枚举或删除。"""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO source_files(source_id, project_id, owner_user_id, kind, original_name, stored_path, size, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  project_id=excluded.project_id,
                  owner_user_id=COALESCE(source_files.owner_user_id, excluded.owner_user_id),
                  stored_path=excluded.stored_path,
                  size=excluded.size
                """,
                (
                    source.get("source_id"),
                    project_id if project_id is not None else source.get("project_id"),
                    source.get("owner_user_id") or None,
                    source.get("kind") or "",
                    source.get("original_name") or "",
                    source.get("stored_path") or "",
                    int(source.get("size") or 0),
                    source.get("created_at") or utcnow_text(),
                ),
            )

    def list_source_files(self, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM source_files WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_source_file(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM source_files WHERE source_id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def source_file_accessible_by(self, source_id: str, user_id: str) -> bool:
        """仅允许上传者或已采用该文件的项目所有者访问。

        第二个条件兼容迁移前没有 ``owner_user_id`` 的项目产物；新上传文件始终记录
        上传者。这里校验的是服务端登记的 source_id，不信任浏览器提交的物理路径。
        """
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM source_files AS source
                LEFT JOIN projects AS project ON project.project_id = source.project_id
                WHERE source.source_id = ?
                  AND (source.owner_user_id = ? OR project.owner_user_id = ?)
                """,
                (source_id, user_id, user_id),
            ).fetchone()
        return bool(row)

    def delete_source_file(self, source_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM source_files WHERE source_id=?", (source_id,))

    def create_user(self, user_id: str, username: str, password_hash: str) -> dict[str, Any]:
        now = utcnow_text()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO users(user_id, username, password_hash, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
                (user_id, username, password_hash, now, now),
            )
        return {"user_id": user_id, "username": username, "created_at": now, "updated_at": now}

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def save_session(self, token: str, user_id: str) -> None:
        """保存不透明会话 token；浏览器只通过 HttpOnly Cookie 持有该值。"""
        now = utcnow_text()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_sessions(token, user_id, created_at, updated_at)
                VALUES(?, ?, COALESCE((SELECT created_at FROM user_sessions WHERE token=?), ?), ?)
                """,
                (token, user_id, token, now, now),
            )

    def get_session_user(self, token: str) -> dict[str, Any] | None:
        """由会话 token 反查用户，是所有受保护 API 的认证起点。"""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT users.* FROM user_sessions
                JOIN users ON users.user_id = user_sessions.user_id
                WHERE user_sessions.token=?
                """,
                (token,),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM user_sessions WHERE token=?", (token,))

    def save_user_ai_settings(self, user_id: str, settings: dict[str, Any]) -> None:
        """按用户保存 AI 配置；响应给浏览器前必须移除真实 API Key。"""
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_ai_settings(user_id, settings_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  settings_json=excluded.settings_json,
                  updated_at=excluded.updated_at
                """,
                (user_id, json.dumps(settings, ensure_ascii=False), utcnow_text()),
            )

    def get_user_ai_settings(self, user_id: str) -> dict[str, Any] | None:
        """读取服务端完整 AI 配置，仅供受信后端调用，不直接原样返回前端。"""
        with self.connect() as conn:
            row = conn.execute("SELECT settings_json FROM user_ai_settings WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["settings_json"] or "{}")
        except ValueError:
            return None
