"""Atomic SQLite persistence for the research scheduler, separate from its source library."""

import json
import sqlite3
from pathlib import Path


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class Store:
    """Engine owns serialization; one transaction records state and its audit artifacts."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS engine_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                schema_version INTEGER NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoint_snapshots (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activities (
                id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
                node_id TEXT, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS node_outputs (
                id TEXT PRIMARY KEY, node_id TEXT NOT NULL, node_version INTEGER NOT NULL,
                round INTEGER NOT NULL, phase TEXT NOT NULL,
                facet TEXT NOT NULL DEFAULT 'research_output', source_node_id TEXT,
                created_at TEXT NOT NULL, valid INTEGER NOT NULL DEFAULT 1,
                payload TEXT NOT NULL, input_payload TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS invalidations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL,
                round INTEGER NOT NULL, node_id TEXT NOT NULL, cause TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requirement_versions (
                id TEXT NOT NULL, version INTEGER NOT NULL, round INTEGER NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(id, version, round)
            );
        """)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(node_outputs)")}
        if "input_payload" not in columns:
            self.connection.execute("ALTER TABLE node_outputs ADD COLUMN input_payload TEXT NOT NULL DEFAULT '{}'")
        self.connection.execute("CREATE INDEX IF NOT EXISTS node_outputs_by_node ON node_outputs(node_id,round,node_version)")
        self.connection.commit()

    def load(self):
        row = self.connection.execute(
            "SELECT schema_version, payload FROM engine_state WHERE singleton=1"
        ).fetchone()
        if row is None:
            return None
        if row[0] != 1:
            raise ValueError("研究状态数据库版本不兼容，请保留原文件并使用对应版本打开")
        return json.loads(row[1])

    def checkpoint(self, checkpoint_id):
        row = self.connection.execute(
            "SELECT payload FROM checkpoint_snapshots WHERE id=?", (checkpoint_id,)
        ).fetchone()
        if row is None:
            raise ValueError("找不到该检查点的持久化快照")
        return json.loads(row[0])

    def save(self, state, epoch, *, library=None, manual_paused=False,
             checkpoints=(), outputs=(), invalidations=()):
        envelope = _json({"state": state, "epoch": epoch, "library": library,
                          "manualPaused": manual_paused})
        round_number = state["project"]["round"]
        with self.connection:
            self.connection.execute(
                "INSERT INTO engine_state VALUES (1,1,?) ON CONFLICT(singleton) "
                "DO UPDATE SET payload=excluded.payload", (envelope,)
            )
            for checkpoint_id in checkpoints:
                self.connection.execute(
                    "INSERT OR IGNORE INTO checkpoint_snapshots VALUES (?,?)",
                    (checkpoint_id, _json(state)),
                )
            self.connection.executemany(
                "INSERT OR IGNORE INTO activities VALUES (?,?,?,?,?)",
                ((entry["id"], entry["at"], entry["actor"], entry.get("nodeId"), _json(entry))
                 for entry in state["activities"]),
            )
            for requirement in state["requirements"]:
                self.connection.execute(
                    "INSERT OR IGNORE INTO requirement_versions VALUES (?,?,?,?)",
                    (requirement["id"], requirement["version"], round_number, _json(requirement)),
                )
            for output in outputs:
                self.connection.execute(
                    "INSERT INTO node_outputs "
                    "(id,node_id,node_version,round,phase,source_node_id,created_at,payload,input_payload) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (output["id"], output["nodeId"], output["version"], round_number,
                     output["phase"], output.get("sourceNodeId"), output["at"], _json(output["output"]),
                     _json({"input": output.get("input", {}), "context": output.get("context", {})})),
                )
            for node_id, cause in invalidations:
                self.connection.execute(
                    "UPDATE node_outputs SET valid=0 WHERE node_id=? AND round=?",
                    (node_id, round_number),
                )
                self.connection.execute(
                    "INSERT INTO invalidations(revision,round,node_id,cause) VALUES (?,?,?,?)",
                    (state["revision"], round_number, node_id, cause),
                )

    def node_history(self, node_id=None):
        sql = "SELECT id,node_id,node_version,round,phase,source_node_id,created_at,valid,payload,input_payload FROM node_outputs"
        parameters = ()
        if node_id is not None:
            sql += " WHERE node_id=?"
            parameters = (str(node_id),)
        rows = self.connection.execute(sql + " ORDER BY rowid", parameters).fetchall()
        results = []
        for row in rows:
            inputs = json.loads(row[9])
            results.append({"id": row[0], "nodeId": row[1], "version": row[2], "round": row[3],
                            "phase": row[4], "sourceNodeId": row[5], "at": row[6], "valid": bool(row[7]),
                            "input": inputs.get("input", {}), "context": inputs.get("context", {}),
                            "output": json.loads(row[8])})
        return results

    def close(self):
        self.connection.close()
