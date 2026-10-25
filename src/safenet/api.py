"""Local processing API. Raw evidence stays in bounded transient memory."""

import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Query, Request
from fastapi import Path as PathParameter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import AwareDatetime
from starlette.exceptions import HTTPException

from safenet import DISCLAIMER, __version__
from safenet.config import load_config
from safenet.contracts import DecisionAction, HealthResponse, JobStatus, Language, RiskLevel, Track
from safenet.errors import ProcessingError
from safenet.http_contracts import (
    CommandRequest,
    DecisionRequest,
    IngestionRequest,
    RetryRequest,
    Work,
    fingerprint,
)
from safenet.preprocessing import iter_records
from safenet.service import ProcessingService, ServiceError
from safenet.storage import SCHEMA_VERSION, connect_database
from safenet.views import case_detail, decisions, export_rows, graph_view, job_views, list_cases


class BodyLimit:
    def __init__(self, app, state):
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = self.state.access_token
        if token:
            headers = dict(scope.get("headers", ()))
            supplied = headers.get(b"x-safenet-access", b"")
            if not hmac.compare_digest(supplied, token.encode("utf-8")):
                response = JSONResponse(
                    status_code=401,
                    content={"error_code": "unauthorized", "disclaimer": DISCLAIMER},
                )
                return await response(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            fragment = message.get("body", b"")
            if len(body) + len(fragment) > self.state.config.limits.max_upload_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={"error_code": "input_too_large", "disclaimer": DISCLAIMER},
                )
                return await response(scope, receive, send)
            body.extend(fragment)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(
    config_path: Path | None = None,
    database_path: Path | None = None,
    tessdata_path: Path | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.access_token = os.environ.get("SAFENET_ACCESS_TOKEN", "")
        app.state.config = load_config(
            config_path or Path(os.environ.get("SAFENET_CONFIG", "config/detector.toml"))
        )
        app.state.database_path = database_path or Path(
            os.environ.get("SAFENET_DB_PATH", "data/safenet.sqlite3")
        )
        service = await asyncio.to_thread(
            ProcessingService,
            app.state.database_path,
            app.state.config,
            tessdata_path or Path(os.environ.get("SAFENET_TESSDATA", ".tools/tessdata")),
        )
        app.state.service = service
        try:
            service.start()
            yield
        finally:
            await asyncio.to_thread(service.close)

    app = FastAPI(
        title="SafeNet KZ",
        description=DISCLAIMER,
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(BodyLimit, state=app.state)

    def error_response(status, code):
        return JSONResponse(
            status_code=status,
            content={"error_code": code, "disclaimer": DISCLAIMER},
            headers={"Retry-After": "1"} if status in (429, 503) else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return error_response(422, "invalid_input")

    @app.exception_handler(ProcessingError)
    async def processing_error(request, error):
        return error_response(422, error.code.value)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return error_response(error.status, error.code)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, error):
        return error_response(503, "database_unavailable")

    @app.exception_handler(sqlite3.IntegrityError)
    async def conflict_error(request, error):
        return error_response(409, "conflict")

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return error_response(
            error.status_code, "not_found" if error.status_code == 404 else "http_error"
        )

    def envelope(value):
        with connect_database(app.state.database_path) as connection:
            jobs = job_views(connection, value["job_ids"])
        return {"request_id": value["request_id"], "jobs": jobs, "disclaimer": DISCLAIMER}

    @app.get("/health", response_model=HealthResponse, summary="Состояние сервиса")
    def health():
        version = 0
        try:
            with connect_database(app.state.database_path) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                connection.execute("SELECT 1 FROM cases LIMIT 1")
            service = app.state.service
            ready = version == SCHEMA_VERSION and service.operational and service.thread.is_alive()
        except sqlite3.Error:
            ready = False
        response = HealthResponse(
            status="ok" if ready else "unavailable",
            detection_ready=ready,
            schema_version=version,
            config_fingerprint=app.state.config.fingerprint,
            code_version=__version__,
        )
        return JSONResponse(
            status_code=200 if ready else 503, content=response.model_dump(mode="json")
        )

    @app.post("/ingestions", status_code=202, summary="Загрузить материалы")
    def ingest(body: IngestionRequest):
        try:
            works = [record.work() for record in body.records]
        except (ValueError, TypeError):
            raise ServiceError(422, "invalid_input") from None
        value = app.state.service.ingest(
            body.request_id, fingerprint(body.model_dump(mode="json", exclude_unset=True)), works
        )
        return envelope(value)

    @app.post("/ingestions/file", status_code=202, summary="Загрузить JSONL или CSV")
    async def ingest_file(request: Request, request_id: UUID, format: Literal["jsonl", "csv"]):
        if request.headers.get("content-encoding", "identity") != "identity":
            raise ServiceError(415, "unsupported_input")
        data = await request.body()

        def submit():
            records = list(iter_records(Path("upload." + format), app.state.config, data=data))
            if any(record.source_kind == "screenshot" for record in records):
                raise ServiceError(415, "screenshot_requires_image")
            works = [Work(record.model_copy(update={"case_id": uuid4()})) for record in records]
            return app.state.service.ingest(
                request_id, fingerprint([format, hashlib.sha256(data).hexdigest()]), works
            )

        return envelope(await asyncio.to_thread(submit))

    @app.get("/ingestions/{request_id}", summary="Состояние загрузки")
    def ingestion(request_id: UUID):
        with connect_database(app.state.database_path) as connection:
            row = connection.execute(
                "SELECT response_json FROM requests WHERE request_id=? AND kind='ingest'",
                (str(request_id),),
            ).fetchone()
        if row is None:
            raise ServiceError(404, "not_found")
        return envelope(json.loads(row[0]))

    @app.get("/jobs/{job_id}", summary="Состояние задания")
    def job(job_id: UUID):
        with connect_database(app.state.database_path) as connection:
            jobs = job_views(connection, [str(job_id)])
        if not jobs:
            raise ServiceError(404, "not_found")
        return {"job": jobs[0], "disclaimer": DISCLAIMER}

    @app.post("/jobs/{job_id}/retry", status_code=202, summary="Повторить задание")
    def retry(job_id: UUID, body: RetryRequest):
        try:
            work = body.record.work() if body.record else None
        except (ValueError, TypeError):
            raise ServiceError(422, "invalid_input") from None
        digest = fingerprint([str(job_id), body.model_dump(mode="json", exclude_unset=True)])
        return envelope(app.state.service.retry(job_id, body.request_id, digest, work))

    @app.post("/cases/{case_id}/reanalyze", status_code=202, summary="Создать новую версию анализа")
    def reanalyze(case_id: UUID, body: CommandRequest):
        return envelope(app.state.service.reanalyze(case_id, body.request_id))

    @app.get("/cases", summary="Очередь материалов")
    def cases(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
        track: Track | None = None,
        level: RiskLevel | None = None,
        language: Language | None = None,
        status: JobStatus | None = None,
        action: DecisionAction | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
    ):
        if since is not None and until is not None and since > until:
            raise ServiceError(422, "invalid_range")
        with connect_database(app.state.database_path) as connection:
            return list_cases(
                connection,
                limit=limit,
                cursor=cursor,
                track=track,
                level=level,
                language=language,
                status=status,
                action=action,
                since=since,
                until=until,
            )

    @app.get("/cases/{case_id}", summary="Карточка материала")
    def case(case_id: UUID):
        with connect_database(app.state.database_path) as connection:
            connection.execute("BEGIN")
            return case_detail(connection, str(case_id))

    @app.get("/cases/{case_id}/results", summary="История версий")
    def results(
        case_id: UUID,
        before: Annotated[int, Query(gt=0)] | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ):
        with connect_database(app.state.database_path) as connection:
            if not connection.execute(
                "SELECT 1 FROM cases WHERE case_id=?", (str(case_id),)
            ).fetchone():
                raise ServiceError(404, "not_found")
            rows = [
                dict(row)
                for row in connection.execute(
                    "SELECT result_id,version,config_fingerprint,"
                    "code_version,created_at FROM analysis_results WHERE case_id=? AND version<? "
                    "ORDER BY version DESC LIMIT ?",
                    (str(case_id), before or 2**63 - 1, limit + 1),
                )
            ]
        return {
            "items": rows[:limit],
            "next_before": rows[limit - 1]["version"] if len(rows) > limit else None,
            "disclaimer": DISCLAIMER,
        }

    @app.get("/cases/{case_id}/results/{version}", summary="Сохранённый результат анализа")
    def result(case_id: UUID, version: Annotated[int, PathParameter(gt=0)]):
        with connect_database(app.state.database_path) as connection:
            row = connection.execute(
                "SELECT result_json FROM analysis_results WHERE case_id=? AND version=?",
                (str(case_id), version),
            ).fetchone()
        if row is None:
            raise ServiceError(404, "not_found")
        return json.loads(row[0])

    @app.get("/cases/{case_id}/image", summary="Обезличенное изображение")
    def image(case_id: UUID):
        with connect_database(app.state.database_path) as connection:
            row = connection.execute(
                "SELECT prepared_json,image_blob FROM case_preparations WHERE case_id=?",
                (str(case_id),),
            ).fetchone()
        if row is None or row[1] is None:
            raise ServiceError(404, "not_found")
        if hashlib.sha256(row[1]).hexdigest() != json.loads(row[0])["artifact_sha256"]:
            raise ServiceError(503, "artifact_unavailable")
        return Response(row[1], media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/cases/{case_id}/graph", summary="Связи материала")
    def graph(
        case_id: UUID,
        result_id: UUID | None = None,
        max_nodes: Annotated[int, Query(ge=1)] | None = None,
        max_edges: Annotated[int, Query(ge=1)] | None = None,
    ):
        limits = app.state.config.graph
        nodes = limits.display_max_nodes if max_nodes is None else max_nodes
        edges = limits.display_max_edges if max_edges is None else max_edges
        if nodes > limits.display_max_nodes or edges > limits.display_max_edges:
            raise ServiceError(422, "invalid_graph_bounds")
        with connect_database(app.state.database_path) as connection:
            view = graph_view(
                connection, str(case_id), str(result_id) if result_id else None, nodes, edges
            )
            ids = [node.value for node in view.nodes if node.kind == "case"]
            timeline = []
            if ids:
                placeholders = ",".join("?" for _ in ids)
                timeline = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT case_id,created_at FROM cases WHERE case_id IN ("
                        + placeholders
                        + ") ORDER BY created_at,case_id",
                        ids,
                    )
                ]
        return {
            "graph": view.model_dump(mode="json"),
            "timeline": timeline,
            "disclaimer": DISCLAIMER,
        }

    @app.post("/cases/{case_id}/decisions", summary="Сохранить решение аналитика")
    def decision(case_id: UUID, body: DecisionRequest):
        return {"decision": app.state.service.decide(case_id, body), "disclaimer": DISCLAIMER}

    @app.get("/cases/{case_id}/decisions", summary="История решений аналитика")
    def history(
        case_id: UUID, limit: Annotated[int, Query(ge=1, le=200)] = 50, cursor: str | None = None
    ):
        with connect_database(app.state.database_path) as connection:
            return decisions(connection, str(case_id), limit, cursor)

    @app.get("/summary", summary="Сводка по материалам")
    def summary():
        service = app.state.service
        with service.graph_lock, connect_database(app.state.database_path) as connection:
            connection.execute("BEGIN")
            statuses = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT j.status,count(*) "
                    "FROM case_index q JOIN jobs j ON j.job_id=q.latest_job_id GROUP BY j.status"
                )
            }
            levels = [
                dict(row)
                for row in connection.execute(
                    "SELECT t.track,t.level,count(*) AS cases "
                    "FROM case_index q JOIN track_scores t ON t.result_id=q.latest_result_id "
                    "GROUP BY t.track,t.level"
                )
            ]
            actions = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT last_action,count(*) FROM case_index "
                    "WHERE last_action IS NOT NULL GROUP BY last_action"
                )
            }
            if service.summary_cache is None:
                service.summary_cache = {
                    "snapshot": service.graph.snapshot.model_dump(mode="json"),
                    "top_components": service.graph.top_components(),
                }
            graph_stats = service.summary_cache
        return {
            "cases": sum(statuses.values()),
            "statuses": statuses,
            "levels": levels,
            "latest_decisions": actions,
            "graph": graph_stats,
            "disclaimer": DISCLAIMER,
        }

    @app.get("/exports", summary="Скачать обезличенный экспорт")
    def export():
        with connect_database(app.state.database_path) as connection:
            connection.execute("SELECT 1 FROM cases LIMIT 1")
        return StreamingResponse(
            export_rows(app.state.database_path),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": "attachment; filename=safenet-sanitized.jsonl",
                "Cache-Control": "no-store",
            },
        )

    return app


app = create_app()
