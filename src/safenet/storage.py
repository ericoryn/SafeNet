"""SQLite schema and short transactional connections."""

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    provenance TEXT NOT NULL CHECK (provenance IN ('synthetic', 'deidentified')),
    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    sanitized_text TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('ru', 'kk', 'mixed', 'unknown')),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('text', 'screenshot', 'transcript')),
    artifact_id TEXT,
    created_at TEXT NOT NULL,
    CHECK (synthetic = (provenance = 'synthetic')),
    CHECK (source_kind != 'screenshot' OR artifact_id IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL CHECK (length(payload_fingerprint) = 64),
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    status TEXT NOT NULL CHECK (
        status IN ('queued','processing','completed','incomplete','failed')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    error_code TEXT CHECK (error_code IN (
        'invalid_input','unsupported_input','ocr_failed','processing_failed',
        'interrupted','not_implemented'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (case_id, job_id),
    CHECK (
        (status IN ('queued','processing','completed') AND error_code IS NULL)
        OR (status IN ('incomplete','failed') AND error_code IS NOT NULL)
    )
);
CREATE TABLE IF NOT EXISTS analysis_results (
    result_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    job_id TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL CHECK (version > 0),
    config_fingerprint TEXT NOT NULL CHECK (length(config_fingerprint) = 64),
    code_version TEXT NOT NULL,
    ocr_version TEXT,
    model_versions_json TEXT NOT NULL CHECK (json_valid(model_versions_json)),
    result_json TEXT NOT NULL CHECK (json_valid(result_json)),
    created_at TEXT NOT NULL,
    UNIQUE (case_id, version),
    UNIQUE (case_id, result_id),
    FOREIGN KEY (case_id, job_id) REFERENCES jobs(case_id, job_id)
);
CREATE TABLE IF NOT EXISTS analyst_decisions (
    decision_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    case_id TEXT NOT NULL,
    result_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('confirm_signs','false_positive','request_review')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (case_id, result_id) REFERENCES analysis_results(case_id, result_id)
);
CREATE TRIGGER IF NOT EXISTS decisions_no_update
BEFORE UPDATE ON analyst_decisions BEGIN
    SELECT RAISE(ABORT, 'decision history is append-only');
END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete
BEFORE DELETE ON analyst_decisions BEGIN
    SELECT RAISE(ABORT, 'decision history is append-only');
END;
CREATE TRIGGER IF NOT EXISTS results_no_update
BEFORE UPDATE ON analysis_results BEGIN
    SELECT RAISE(ABORT, 'analysis results are immutable');
END;
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS decisions_case_time ON analyst_decisions(case_id, created_at);
"""

SERVICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_contexts (
    context_id TEXT PRIMARY KEY, config_json TEXT NOT NULL CHECK(json_valid(config_json)),
    code_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('ingest','reanalyze','retry','decision')),
    payload_fingerprint TEXT NOT NULL CHECK(length(payload_fingerprint)=64),
    response_json TEXT NOT NULL CHECK(json_valid(response_json))
);
CREATE TABLE IF NOT EXISTS case_preparations (
    case_id TEXT PRIMARY KEY REFERENCES cases(case_id),
    prepared_json TEXT NOT NULL CHECK(json_valid(prepared_json)), image_blob BLOB
);
CREATE TABLE IF NOT EXISTS job_options (
    ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id), version INTEGER NOT NULL CHECK(version>0),
    context_id TEXT NOT NULL REFERENCES graph_contexts(context_id)
);
CREATE TABLE IF NOT EXISTS case_index (
    case_id TEXT PRIMARY KEY REFERENCES cases(case_id), latest_job_id TEXT NOT NULL,
    latest_result_id TEXT, queue_score REAL CHECK(queue_score BETWEEN 0 AND 100),
    queue_level TEXT CHECK(queue_level IN ('low','medium','high')), last_action TEXT,
    FOREIGN KEY(case_id,latest_job_id) REFERENCES jobs(case_id,job_id),
    FOREIGN KEY(case_id,latest_result_id) REFERENCES analysis_results(case_id,result_id)
);
CREATE TABLE IF NOT EXISTS track_scores (
    result_id TEXT NOT NULL REFERENCES analysis_results(result_id),
    track TEXT NOT NULL CHECK(track IN ('lottery','casino','investment')),
    score REAL NOT NULL CHECK(score BETWEEN 0 AND 100),
    level TEXT NOT NULL CHECK(level IN ('low','medium','high')), PRIMARY KEY(result_id,track)
);
CREATE TABLE IF NOT EXISTS graph_events (
    context_id TEXT NOT NULL REFERENCES graph_contexts(context_id), sequence INTEGER NOT NULL,
    case_id TEXT NOT NULL REFERENCES case_preparations(case_id),
    base_json TEXT NOT NULL CHECK(json_valid(base_json)), PRIMARY KEY(context_id,sequence),
    UNIQUE(context_id,case_id)
);
CREATE TABLE IF NOT EXISTS graph_snapshots (
    context_id TEXT NOT NULL REFERENCES graph_contexts(context_id), sequence INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)), PRIMARY KEY(context_id,sequence)
);
CREATE TABLE IF NOT EXISTS graph_nodes (
    context_id TEXT NOT NULL REFERENCES graph_contexts(context_id), node_id TEXT NOT NULL,
    node_json TEXT NOT NULL CHECK(json_valid(node_json)), PRIMARY KEY(context_id,node_id)
);
CREATE TABLE IF NOT EXISTS graph_edges (
    context_id TEXT NOT NULL, case_id TEXT NOT NULL, target TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    edge_json TEXT NOT NULL CHECK(json_valid(edge_json)), PRIMARY KEY(context_id,case_id,target),
    FOREIGN KEY(context_id,target) REFERENCES graph_nodes(context_id,node_id),
    FOREIGN KEY(context_id,case_id) REFERENCES graph_events(context_id,case_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active_case ON jobs(case_id)
    WHERE status IN ('queued','processing');
CREATE INDEX IF NOT EXISTS jobs_case ON jobs(case_id,created_at,job_id);
CREATE INDEX IF NOT EXISTS case_queue ON case_index(coalesce(queue_score,-1) DESC,case_id);
CREATE INDEX IF NOT EXISTS cases_language_date ON cases(language,created_at,case_id);
CREATE INDEX IF NOT EXISTS scores_filter ON track_scores(track,level,result_id);
CREATE INDEX IF NOT EXISTS graph_neighbors ON graph_edges(context_id,target,sequence,case_id);
CREATE TRIGGER IF NOT EXISTS results_no_delete
BEFORE DELETE ON analysis_results BEGIN SELECT RAISE(ABORT,'analysis results are immutable'); END;
"""


@contextmanager
def connect_database(path: Path, *, create: bool = False) -> Iterator[sqlite3.Connection]:
    uri = f"{path.resolve().as_uri()}?mode={'rwc' if create else 'rw'}"
    # Windows can briefly reject WAL truncation just after a process is killed.
    for attempt in range(3):
        connection = sqlite3.connect(uri, uri=True, timeout=0.25)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA user_version").fetchone()
            break
        except sqlite3.OperationalError as error:
            connection.close()
            if error.sqlite_errorcode != sqlite3.SQLITE_IOERR_TRUNCATE or attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path, create=True) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, SCHEMA_VERSION):
            raise RuntimeError("unsupported database schema version; migration required")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            f"BEGIN IMMEDIATE;\n{SCHEMA}\n{SERVICE_SCHEMA}\n"
            f"PRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
        )
