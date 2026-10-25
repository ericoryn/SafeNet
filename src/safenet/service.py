"""Single-owner durable queue. Raw work exists only in bounded process memory."""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from safenet import __version__
from safenet.config import DetectorConfig
from safenet.contracts import AnalysisResult, AnalystDecision, ErrorCode, PreparedContent
from safenet.errors import ProcessingError
from safenet.graph import GraphIndex, node_id
from safenet.http_contracts import DecisionRequest, Work, fingerprint
from safenet.ocr import OCRProcessor
from safenet.pipeline import analyze
from safenet.preprocessing import preprocess_record, restore_prepared
from safenet.process_lock import ProcessLock
from safenet.storage import connect_database, initialize_database


def now() -> str:
    return datetime.now(UTC).isoformat()


class ServiceError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


class ProcessingService:
    def __init__(self, database: Path, config: DetectorConfig, tessdata: Path):
        self.database, self.config, self.tessdata = database, config, tessdata
        self.owner = ProcessLock(database)
        self.owner.acquire()
        self.state_lock = threading.RLock()
        self.graph_lock = threading.RLock()
        self.raw: dict[str, Work] = {}
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.operational = True
        self.processor = None
        self.context_id = fingerprint([__version__, config.fingerprint, "demo", "graph-v1"])
        self.summary_cache = None
        try:
            self.processor = OCRProcessor(config, tessdata)
            initialize_database(database)
            with connect_database(database) as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO graph_contexts VALUES (?,?,?)",
                    (self.context_id, config.model_dump_json(), __version__),
                )
            self._adopt_foundation_rows()
            self.rebuild_graph()
            self.recover()
        except BaseException:
            self.owner.close()
            raise

    def start(self):
        self.thread = threading.Thread(target=self._run, name="safenet-worker", daemon=True)
        self.thread.start()

    def _adopt_foundation_rows(self):
        """Schema-1 metadata stays visible; missing preparations are never invented."""
        with connect_database(self.database) as connection:
            missing_cases = connection.execute(
                "SELECT 1 FROM cases c LEFT JOIN case_index q USING(case_id) "
                "WHERE q.case_id IS NULL LIMIT 1"
            ).fetchone()
            missing_jobs = connection.execute(
                "SELECT 1 FROM jobs j LEFT JOIN job_options o USING(job_id) "
                "WHERE o.job_id IS NULL LIMIT 1"
            ).fetchone()
            if missing_cases is None and missing_jobs is None:
                return
            connection.execute("BEGIN IMMEDIATE")
            orphans = connection.execute(
                "SELECT c.case_id FROM cases c WHERE NOT EXISTS "
                "(SELECT 1 FROM jobs j WHERE j.case_id=c.case_id)"
            ).fetchall()
            stamp = now()
            connection.executemany(
                "INSERT INTO jobs VALUES (?,?,?,?,'incomplete',0,'interrupted',?,?)",
                [
                    (
                        str(uuid4()),
                        str(uuid4()),
                        fingerprint([row[0], "legacy"]),
                        row[0],
                        stamp,
                        stamp,
                    )
                    for row in orphans
                ],
            )
            connection.execute(
                "INSERT INTO job_options(job_id,version,context_id) "
                "SELECT j.job_id,coalesce(r.version,1),? FROM jobs j "
                "LEFT JOIN analysis_results r USING(job_id) "
                "WHERE NOT EXISTS (SELECT 1 FROM job_options o WHERE o.job_id=j.job_id) "
                "ORDER BY j.created_at,j.job_id",
                (self.context_id,),
            )
            connection.execute(
                "INSERT OR IGNORE INTO case_index(case_id,latest_job_id,latest_result_id) "
                "SELECT c.case_id,(SELECT j.job_id FROM jobs j JOIN job_options o USING(job_id) "
                "WHERE j.case_id=c.case_id ORDER BY o.ordinal DESC LIMIT 1),"
                "(SELECT r.result_id FROM analysis_results r WHERE r.case_id=c.case_id "
                "ORDER BY r.version DESC LIMIT 1) FROM cases c"
            )
            connection.execute(
                "INSERT OR IGNORE INTO track_scores "
                "SELECT r.result_id,json_extract(t.value,'$.track'),"
                "json_extract(t.value,'$.score'),json_extract(t.value,'$.level') "
                "FROM analysis_results r,json_each(r.result_json,'$.tracks') t"
            )
            connection.execute(
                "UPDATE case_index SET queue_score=(SELECT max(t.score) FROM track_scores t "
                "WHERE t.result_id=case_index.latest_result_id),"
                "queue_level=(SELECT t.level FROM track_scores t "
                "WHERE t.result_id=case_index.latest_result_id "
                "ORDER BY t.score DESC,t.track LIMIT 1),"
                "last_action=(SELECT d.action FROM analyst_decisions d "
                "WHERE d.case_id=case_index.case_id "
                "ORDER BY d.created_at DESC,d.decision_id DESC LIMIT 1)"
            )

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join(self.config.ocr.timeout_seconds + 10)
            if self.thread.is_alive():
                raise RuntimeError("worker did not stop; database ownership retained")
        self.raw.clear()
        self.owner.close()

    def rebuild_graph(self):
        graph = GraphIndex(self.config, "demo")
        with connect_database(self.database) as connection:
            rows = connection.execute(
                "SELECT e.base_json,p.prepared_json,s.snapshot_json "
                "FROM graph_events e JOIN case_preparations p USING(case_id) "
                "JOIN graph_snapshots s ON s.context_id=e.context_id AND s.sequence=e.sequence "
                "WHERE e.context_id=? ORDER BY e.sequence",
                (self.context_id,),
            )
            for row in rows:
                graph.process(
                    PreparedContent.model_validate_json(row["prepared_json"]),
                    AnalysisResult.model_validate_json(row["base_json"]),
                    scope="demo",
                )
                if graph.snapshot.model_dump(mode="json") != json.loads(row["snapshot_json"]):
                    raise RuntimeError("graph recovery integrity failure")
        with connect_database(self.database) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO graph_snapshots VALUES (?,0,?)",
                (self.context_id, graph.snapshots[0].model_dump_json()),
            )
        self.graph = graph
        self.summary_cache = None

    def recover(self, *, live: bool = False):
        with self.state_lock, connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT j.job_id,j.status,j.attempts,o.context_id,"
                "p.case_id AS prepared FROM jobs j LEFT JOIN job_options o USING(job_id) "
                "LEFT JOIN case_preparations p USING(case_id) "
                "WHERE j.status IN ('queued','processing')"
            ).fetchall()
            for row in rows:
                if live and row["status"] == "queued":
                    continue
                retryable = row["prepared"] is not None or (live and row["job_id"] in self.raw)
                compatible = row["context_id"] == self.context_id
                exhausted = row["attempts"] >= self.config.processing.max_attempts
                status = (
                    "queued"
                    if retryable and compatible and not exhausted
                    else ("failed" if exhausted else "incomplete")
                )
                code = (
                    None
                    if status == "queued"
                    else ("interrupted" if compatible else "processing_failed")
                )
                connection.execute(
                    "UPDATE jobs SET status=?,error_code=?,updated_at=? WHERE job_id=?",
                    (status, code, now(), row["job_id"]),
                )

    def _existing_request(self, connection, request_id, kind, digest):
        row = connection.execute(
            "SELECT * FROM requests WHERE request_id=?", (str(request_id),)
        ).fetchone()
        if row is None:
            return None
        if row["kind"] != kind or row["payload_fingerprint"] != digest:
            raise ServiceError(409, "request_conflict")
        return json.loads(row["response_json"])

    def _remember(self, connection, request_id, kind, digest, response):
        connection.execute(
            "INSERT INTO requests VALUES (?,?,?,?)",
            (str(request_id), kind, digest, json.dumps(response)),
        )

    def _capacity(self, connection, count, size=0):
        pending = connection.execute(
            "SELECT count(*) FROM jobs WHERE status IN ('queued','processing')"
        ).fetchone()[0]
        if (
            pending + count > self.config.limits.max_pending_jobs
            or sum(work.size for work in self.raw.values()) + size
            > self.config.processing.max_pending_bytes
        ):
            raise ServiceError(429, "capacity_exceeded")

    def _insert_job(self, connection, case_id, request_id, digest):
        active = connection.execute(
            "SELECT 1 FROM jobs WHERE case_id=? AND status IN ('queued','processing')", (case_id,)
        ).fetchone()
        if active:
            raise ServiceError(409, "case_busy")
        version = connection.execute(
            "SELECT coalesce(max(o.version),0)+1 FROM job_options o "
            "JOIN jobs j USING(job_id) WHERE j.case_id=?",
            (case_id,),
        ).fetchone()[0]
        job_id, stamp = str(uuid4()), now()
        connection.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,'queued',0,NULL,?,?)",
            (job_id, str(request_id), digest, case_id, stamp, stamp),
        )
        connection.execute(
            "INSERT INTO job_options(job_id,version,context_id) VALUES (?,?,?)",
            (job_id, version, self.context_id),
        )
        connection.execute(
            "INSERT INTO case_index(case_id,latest_job_id) VALUES (?,?) "
            "ON CONFLICT(case_id) DO UPDATE SET latest_job_id=excluded.latest_job_id",
            (case_id, job_id),
        )
        return job_id

    def ingest(self, request_id: UUID, digest: str, works: list[Work]) -> dict:
        if not 0 < len(works) <= self.config.limits.max_records:
            raise ServiceError(422, "invalid_input")
        sizes = [work.size for work in works]
        hashes = [work.fingerprint for work in works]
        if any(len(work.record.text) > self.config.limits.max_text_characters for work in works):
            raise ServiceError(422, "invalid_input")
        staged = {}
        with self.state_lock, connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._existing_request(connection, request_id, "ingest", digest)
            if existing is not None:
                return existing
            self._capacity(connection, len(works), sum(sizes))
            for index, (work, source_hash) in enumerate(zip(works, hashes, strict=True)):
                record = work.record
                case_id = str(record.case_id)
                connection.execute(
                    "INSERT INTO cases VALUES (?,?,?,'','unknown',?,?,?)",
                    (
                        case_id,
                        record.provenance,
                        int(record.synthetic),
                        record.source_kind,
                        str(record.artifact_id) if record.artifact_id else None,
                        record.created_at.astimezone(UTC).isoformat(),
                    ),
                )
                job_id = self._insert_job(
                    connection, case_id, uuid5(request_id, str(index)), source_hash
                )
                staged[job_id] = work
            response = {"request_id": str(request_id), "job_ids": list(staged)}
            self._remember(connection, request_id, "ingest", digest, response)
            connection.commit()
            self.raw.update(staged)
        self.wake.set()
        return response

    def reanalyze(self, case_id: UUID, request_id: UUID) -> dict:
        case_id = str(case_id)
        digest = fingerprint([case_id, "reanalyze"])
        with self.state_lock, connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._existing_request(connection, request_id, "reanalyze", digest)
            if existing is not None:
                return existing
            row = connection.execute(
                "SELECT j.payload_fingerprint FROM case_preparations p "
                "JOIN case_index q USING(case_id) JOIN jobs j ON j.job_id=q.latest_job_id "
                "WHERE p.case_id=?",
                (case_id,),
            ).fetchone()
            if row is None:
                exists = connection.execute(
                    "SELECT 1 FROM cases WHERE case_id=?", (case_id,)
                ).fetchone()
                raise ServiceError(
                    409 if exists else 404, "source_required" if exists else "not_found"
                )
            self._capacity(connection, 1)
            job_id = self._insert_job(connection, case_id, request_id, row[0])
            response = {"request_id": str(request_id), "job_ids": [job_id]}
            self._remember(connection, request_id, "reanalyze", digest, response)
        self.wake.set()
        return response

    def retry(self, job_id: UUID, request_id: UUID, digest: str, work: Work | None) -> dict:
        job_id = str(job_id)
        with self.state_lock, connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._existing_request(connection, request_id, "retry", digest)
            if existing is not None:
                return existing
            row = connection.execute(
                "SELECT j.*,o.context_id,o.version,c.artifact_id,c.created_at AS source_date,"
                "p.case_id AS prepared FROM jobs j JOIN job_options o USING(job_id) "
                "JOIN cases c USING(case_id) LEFT JOIN case_preparations p USING(case_id) "
                "WHERE j.job_id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ServiceError(404, "not_found")
            if row["status"] not in ("failed", "incomplete"):
                raise ServiceError(409, "job_not_retryable")
            if row["attempts"] >= self.config.processing.max_attempts:
                raise ServiceError(409, "retry_exhausted")
            latest = connection.execute(
                "SELECT max(o.version) FROM jobs j JOIN job_options o USING(job_id) "
                "WHERE j.case_id=?",
                (row["case_id"],),
            ).fetchone()[0]
            if row["version"] < latest:
                raise ServiceError(409, "job_superseded")
            if row["context_id"] != self.context_id:
                raise ServiceError(409, "configuration_changed")
            if connection.execute(
                "SELECT 1 FROM jobs WHERE case_id=? AND status IN ('queued','processing')",
                (row["case_id"],),
            ).fetchone():
                raise ServiceError(409, "case_busy")
            if work is not None and work.fingerprint != row["payload_fingerprint"]:
                raise ServiceError(409, "source_conflict")
            if row["prepared"] is None and work is None:
                raise ServiceError(409, "source_required")
            self._capacity(connection, 1, work.size if work and row["prepared"] is None else 0)
            connection.execute(
                "UPDATE jobs SET status='queued',error_code=NULL,updated_at=? WHERE job_id=?",
                (now(), job_id),
            )
            connection.execute(
                "UPDATE case_index SET latest_job_id=? WHERE case_id=?", (job_id, row["case_id"])
            )
            response = {"request_id": str(request_id), "job_ids": [job_id]}
            self._remember(connection, request_id, "retry", digest, response)
            connection.commit()
            if work is not None and row["prepared"] is None:
                record = work.record.model_copy(
                    update={
                        "case_id": UUID(row["case_id"]),
                        "artifact_id": UUID(row["artifact_id"]) if row["artifact_id"] else None,
                        "created_at": datetime.fromisoformat(row["source_date"]),
                    }
                )
                self.raw[job_id] = Work(record, work.image)
        self.wake.set()
        return response

    def decide(self, case_id: UUID, request: DecisionRequest) -> dict:
        digest = fingerprint([str(case_id), str(request.result_id), request.action.value])
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = self._existing_request(connection, request.request_id, "decision", digest)
            if old is not None:
                return old
            if not connection.execute(
                "SELECT 1 FROM analysis_results WHERE case_id=? AND result_id=?",
                (str(case_id), str(request.result_id)),
            ).fetchone():
                raise ServiceError(404, "not_found")
            stamp = datetime.fromisoformat(now())
            previous = connection.execute(
                "SELECT created_at FROM analyst_decisions WHERE case_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (str(case_id),),
            ).fetchone()
            if previous:
                # Сохраняем порядок добавления при одинаковых отсчётах или откате часов ОС.
                stamp = max(stamp, datetime.fromisoformat(previous[0]) + timedelta(microseconds=1))
            decision = AnalystDecision(case_id=case_id, created_at=stamp, **request.model_dump())
            connection.execute(
                "INSERT INTO analyst_decisions VALUES (?,?,?,?,?,?)",
                (
                    str(decision.decision_id),
                    str(decision.request_id),
                    str(case_id),
                    str(decision.result_id),
                    decision.action.value,
                    decision.created_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE case_index SET last_action=? WHERE case_id=?",
                (decision.action.value, str(case_id)),
            )
            response = decision.model_dump(mode="json")
            self._remember(connection, request.request_id, "decision", digest, response)
        return response

    def claim(self):
        with self.state_lock, connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT j.*,o.version FROM jobs j JOIN job_options o USING(job_id) "
                "WHERE j.status='queued' AND o.context_id=? ORDER BY o.ordinal LIMIT 1",
                (self.context_id,),
            ).fetchone()
            if row is None:
                return None
            if row["attempts"] >= self.config.processing.max_attempts:
                connection.execute(
                    "UPDATE jobs SET status='failed',error_code='interrupted',updated_at=? "
                    "WHERE job_id=?",
                    (now(), row["job_id"]),
                )
                return None
            connection.execute(
                "UPDATE jobs SET status='processing',attempts=attempts+1,updated_at=? "
                "WHERE job_id=?",
                (now(), row["job_id"]),
            )
            return dict(row)

    def _prepared(self, job):
        with connect_database(self.database) as connection:
            row = connection.execute(
                "SELECT prepared_json,image_blob FROM case_preparations WHERE case_id=?",
                (job["case_id"],),
            ).fetchone()
        if row is not None:
            return restore_prepared(row[0], row[1], self.config)
        with self.state_lock:
            work = self.raw.get(job["job_id"])
        if work is None:
            raise ProcessingError(ErrorCode.INTERRUPTED)
        if work.image is not None and self.processor is None:
            self.processor = OCRProcessor(self.config, self.tessdata)
        prepared = preprocess_record(
            work.record, self.config, image_bytes=work.image, ocr=self.processor
        )
        encoded = prepared.model_dump_json()
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE cases SET sanitized_text=?,language=? WHERE case_id=?",
                (prepared.content.text, prepared.content.language.value, job["case_id"]),
            )
            connection.execute(
                "INSERT INTO case_preparations VALUES (?,?,?)",
                (job["case_id"], encoded, prepared.sanitized_image),
            )
        return prepared

    def save_result(self, job, base: AnalysisResult, result: AnalysisResult, new_event: bool):
        # OCR, matching, graph calculations and JSON serialization precede the write transaction.
        result_json, base_json = result.model_dump_json(), base.model_dump_json()
        snapshot_json = self.graph.snapshot.model_dump_json()
        edges = tuple(self.graph.edges[base.case_id].values()) if new_event else ()
        ids = (
            {node_id("case", str(base.case_id)), *(edge.target for edge in edges)}
            if new_event
            else set()
        )
        nodes = [(self.context_id, key, self.graph.nodes[key].model_dump_json()) for key in ids]
        edge_rows = [
            (
                self.context_id,
                job["case_id"],
                edge.target,
                edge.observed_sequence,
                edge.model_dump_json(),
            )
            for edge in edges
        ]
        with connect_database(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "SELECT status FROM jobs WHERE job_id=?", (job["job_id"],)
                ).fetchone()[0]
                != "processing"
            ):
                raise RuntimeError("job ownership changed")
            if new_event:
                sequence = self.graph.snapshot.sequence
                connection.execute(
                    "INSERT INTO graph_events VALUES (?,?,?,?)",
                    (self.context_id, sequence, job["case_id"], base_json),
                )
                connection.execute(
                    "INSERT INTO graph_snapshots VALUES (?,?,?)",
                    (self.context_id, sequence, snapshot_json),
                )
                connection.executemany("INSERT OR IGNORE INTO graph_nodes VALUES (?,?,?)", nodes)
                connection.executemany("INSERT INTO graph_edges VALUES (?,?,?,?,?)", edge_rows)
            connection.execute(
                "INSERT INTO analysis_results VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(result.result_id),
                    job["case_id"],
                    job["job_id"],
                    result.version,
                    result.run.config_fingerprint,
                    result.run.code_version,
                    result.run.ocr_version,
                    json.dumps(result.run.model_versions),
                    result_json,
                    result.created_at.isoformat(),
                ),
            )
            connection.executemany(
                "INSERT INTO track_scores VALUES (?,?,?,?)",
                [
                    (str(result.result_id), track.track.value, track.score, track.level.value)
                    for track in result.tracks
                ],
            )
            connection.execute(
                "UPDATE case_index SET latest_result_id=?,queue_score=?,queue_level=? "
                "WHERE case_id=?",
                (
                    str(result.result_id),
                    result.queue_score,
                    result.queue_level.value,
                    job["case_id"],
                ),
            )
            connection.execute(
                "UPDATE jobs SET status='completed',error_code=NULL,updated_at=? WHERE job_id=?",
                (now(), job["job_id"]),
            )

    def _process(self, job):
        content = self._prepared(job)
        outcome = analyze(content, self.config, version=job["version"])
        if outcome.result is None:
            raise ProcessingError(outcome.error_code)
        base = outcome.result
        with self.graph_lock:
            new = base.case_id not in self.graph.case_sequences
            result = (
                self.graph.process(content, base, scope="demo")
                if new
                else self.graph.preview(content, base, scope="demo")
            )
            self.save_result(job, base, result, new)
            self.summary_cache = None

    def _fail(self, job, code):
        status = "incomplete" if code in (ErrorCode.OCR_FAILED, ErrorCode.INTERRUPTED) else "failed"
        with connect_database(self.database) as connection:
            connection.execute(
                "UPDATE jobs SET status=?,error_code=?,updated_at=? "
                "WHERE job_id=? AND status='processing'",
                (status, code.value, now(), job["job_id"]),
            )

    def _discard_finished_raw(self):
        with self.state_lock, connect_database(self.database) as connection:
            active = {
                row[0]
                for row in connection.execute(
                    "SELECT job_id FROM jobs WHERE status IN ('queued','processing')"
                )
            }
            for job_id in list(self.raw):
                if job_id not in active:
                    self.raw.pop(job_id)

    def _run(self):
        recovering = False
        while not self.stop.is_set():
            try:
                if recovering:
                    with self.graph_lock:
                        self.rebuild_graph()
                    self.recover(live=True)
                    recovering = False
                job = self.claim()
                if job is not None:
                    try:
                        self._process(job)
                    except sqlite3.OperationalError:
                        raise
                    except Exception as error:
                        with self.graph_lock:
                            self.rebuild_graph()
                        self._fail(
                            job,
                            error.code
                            if isinstance(error, ProcessingError)
                            else ErrorCode.PROCESSING_FAILED,
                        )
                self._discard_finished_raw()
                self.operational = True
                if job is None:
                    self.wake.wait(0.2)
                    self.wake.clear()
            except sqlite3.Error:
                self.operational = False
                recovering = True
                self.stop.wait(0.2)
            except Exception:
                self.operational = False
                return
