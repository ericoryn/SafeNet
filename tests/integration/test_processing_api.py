import base64
import csv
import io
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from safenet import DISCLAIMER
from safenet.api import create_app
from safenet.contracts import AnalysisResult, IncomingRecord
from safenet.http_contracts import UploadRecord, fingerprint
from safenet.service import ProcessingService, ServiceError
from safenet.storage import connect_database

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config/detector.toml"
MODELS = ROOT / ".tools/tessdata"
HIGH = "Гарантированный доход. Пригласи друзей. https://shared.test/"
MEDIUM = "Гарантированный доход. Успей сегодня. https://shared.test/"


def upload(text=HIGH, **changes):
    return {"provenance": "synthetic", "synthetic": True, "text": text, **changes}


def submit(client, records=None, request_id=None):
    body = {"request_id": request_id or str(uuid4()), "records": records or [upload()]}
    response = client.post("/ingestions", json=body)
    assert response.status_code == 202, response.text
    return body, response.json()


def wait_job(client, job_id, timeout=15):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        response = client.get("/jobs/" + job_id)
        assert response.status_code == 200, response.text
        job = response.json()["job"]
        if job["status"] not in ("queued", "processing"):
            return job
        time.sleep(0.01)
    pytest.fail("job did not finish")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(CONFIG, tmp_path / "api.sqlite3")) as client:
        yield client


def test_cloud_access_boundary_and_proxy_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SAFENET_ACCESS_TOKEN", "synthetic-test-secret")
    with TestClient(
        create_app(CONFIG, tmp_path / "protected.sqlite3"), root_path="/backend"
    ) as client:
        for headers in ({}, {"X-SafeNet-Access": "wrong"}):
            response = client.get("/health", headers=headers)
            assert response.status_code == 401
            assert "synthetic-test-secret" not in response.text
            assert response.json()["disclaimer"] == DISCLAIMER
        headers = {"X-SafeNet-Access": "synthetic-test-secret"}
        assert client.get("/health", headers=headers).status_code == 200
        schema = client.get("/openapi.json", headers=headers).json()
        assert schema["servers"] == [{"url": "/backend"}]
        assert "/backend/openapi.json" in client.get("/docs", headers=headers).text
        assert client.post("/ingestions", content=b"invalid").status_code == 401


def test_queue_preview_is_sanitized_bounded_and_available_without_evidence(client):
    _, response = submit(
        client, [upload("Гарантированный доход. +7 000 111 22 33; " + "пример " * 60)]
    )
    job = wait_job(client, response["jobs"][0]["job_id"])
    assert job["status"] == "completed"
    item = client.get("/cases").json()["items"][0]
    assert len(item["preview"]) == 160
    assert item["preview"].startswith("Гарантированный доход.")
    assert "111 22" not in item["preview"]
    assert "prepared" not in item and "latest_result" not in item


def test_complete_text_flow_versions_feedback_and_restart(tmp_path):
    database = tmp_path / "api.sqlite3"
    with TestClient(create_app(CONFIG, database)) as client:
        body, response = submit(client)
        first = wait_job(client, response["jobs"][0]["job_id"])
        assert first["status"] == "completed" and first["attempts"] == 1
        case_id = first["case_id"]
        original = client.get(f"/cases/{case_id}/results/1").json()
        assert AnalysisResult.model_validate(original).queue_score == 90
        duplicate = client.post("/ingestions", json=body).json()
        assert duplicate["jobs"][0]["job_id"] == first["job_id"]
        assert (
            client.post(
                "/ingestions", json={**body, "records": [upload("Изменённый текст")]}
            ).status_code
            == 409
        )
        decision = {
            "request_id": str(uuid4()),
            "result_id": original["result_id"],
            "action": "confirm_signs",
        }
        saved = client.post(f"/cases/{case_id}/decisions", json=decision)
        assert saved.status_code == 200, saved.text
        assert client.post(f"/cases/{case_id}/decisions", json=decision).json() == saved.json()
        assert (
            client.post(
                f"/cases/{case_id}/decisions", json={**decision, "action": "false_positive"}
            ).status_code
            == 409
        )
        request = {"request_id": str(uuid4())}
        reanalysis = client.post(f"/cases/{case_id}/reanalyze", json=request)
        second = wait_job(client, reanalysis.json()["jobs"][0]["job_id"])
        assert second["status"] == "completed" and second["version"] == 2
        assert (
            client.post(f"/cases/{case_id}/reanalyze", json=request).json()["jobs"][0]["job_id"]
            == second["job_id"]
        )
        assert client.get(f"/cases/{case_id}/results/1").json() == original
        assert len(client.get(f"/cases/{case_id}/results").json()["items"]) == 2
        assert client.get("/summary").json()["graph"]["snapshot"]["sequence"] == 1
        assert client.get(f"/cases/{case_id}/graph").status_code == 200
        assert client.get(f"/cases/{case_id}/graph?max_nodes=1").json()["graph"]["truncated"]
    with TestClient(create_app(CONFIG, database)) as restarted:
        assert (
            restarted.get(f"/cases/{case_id}/decisions").json()["items"][0]["decision_id"]
            == saved.json()["decision"]["decision_id"]
        )
        assert restarted.get(f"/cases/{case_id}/results/1").json() == original
        assert restarted.get("/summary").json()["graph"]["snapshot"]["sequence"] == 1
        assert (
            restarted.post("/ingestions", json=body).json()["jobs"][0]["job_id"] == first["job_id"]
        )


def test_queue_keyset_filters_graph_temporal_context_and_summary(client):
    _, response = submit(
        client, [upload(HIGH), upload(MEDIUM), upload("Тегін байқау, қатысу тегін.")]
    )
    jobs = [wait_job(client, row["job_id"]) for row in response["jobs"]]
    assert all(job["status"] == "completed" for job in jobs)
    cases = client.get("/cases?limit=1").json()
    seen = cases["items"]
    while cases["next_cursor"]:
        cases = client.get("/cases", params={"limit": 1, "cursor": cases["next_cursor"]}).json()
        seen += cases["items"]
    assert len({item["case_id"] for item in seen}) == 3
    assert [item["queue_score"] for item in seen] == [90, 70, 0]
    assert len(client.get("/cases?track=investment&level=high").json()["items"]) == 2
    assert len(client.get("/cases?language=kk&status=completed").json()["items"]) == 1
    assert client.get("/cases?since=2030-01-01T00:00:00Z").json()["items"] == []
    first_id, second_id = jobs[0]["case_id"], jobs[1]["case_id"]
    before = client.get(f"/cases/{first_id}/graph").json()["graph"]
    after = client.get(f"/cases/{second_id}/graph").json()["graph"]
    assert before["snapshot"]["sequence"] == 1 and after["snapshot"]["sequence"] == 2
    assert not any(node["value"] == second_id for node in before["nodes"])
    summary = client.get("/summary").json()
    assert summary["cases"] == 3 and summary["statuses"] == {"completed": 3}
    assert client.get("/cases?cursor=invalid").status_code == 422
    assert client.get("/cases?limit=201").status_code == 422
    assert client.get(f"/cases/{first_id}/graph?max_edges=201").status_code == 422
    foreign = client.get(f"/cases/{first_id}/results/1").json()["result_id"]
    assert (
        client.post(
            f"/cases/{second_id}/decisions",
            json={"request_id": str(uuid4()), "result_id": foreign, "action": "false_positive"},
        ).status_code
        == 404
    )


@pytest.mark.parametrize("format", ["jsonl", "csv"])
def test_file_ingestion_uses_memory_parser_and_rejects_truth_fields(client, format):
    row = IncomingRecord(provenance="synthetic", synthetic=True, text=HIGH).model_dump(
        mode="json", exclude_none=True
    )
    if format == "jsonl":
        data = json.dumps(row) + "\n"
    else:
        target = io.StringIO(newline="")
        writer = csv.DictWriter(target, fieldnames=list(row))
        writer.writeheader()
        writer.writerow({**row, "synthetic": "true", "redirect_chain": "[]"})
        data = target.getvalue()
    params = {"request_id": str(uuid4()), "format": format}
    response = client.post("/ingestions/file", params=params, content=data.encode())
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["jobs"][0]["job_id"])
    assert job["status"] == "completed" and job["case_id"] != row["case_id"]
    assert (
        client.post("/ingestions/file", params=params, content=data.encode()).json()["jobs"][0][
            "job_id"
        ]
        == job["job_id"]
    )
    bad = client.post(
        "/ingestions/file",
        params={**params, "request_id": str(uuid4())},
        content=b'{"label":"PRIVATE-SENTINEL"}',
    )
    assert bad.status_code == 422 and "PRIVATE-SENTINEL" not in bad.text


def test_raw_privacy_sentinels_absent_from_database_responses_and_export(client, tmp_path, caplog):
    text = (
        "ФИО: Тестов Тест Тестович; +7 000 111 22 33; 0000 1111 2222 3333; "
        "private_sentinel@example.test; https://safe.test/private?ref=SECRET_SENTINEL. "
        "Взнос. Случайный выбор."
    )
    _, response = submit(client, [upload(text)])
    job = wait_job(client, response["jobs"][0]["job_id"])
    detail = client.get("/cases/" + job["case_id"])
    exported = client.get("/exports")
    assert detail.status_code == exported.status_code == 200
    sentinels = [
        "Тестов",
        "+7 000 111 22 33",
        "0000 1111 2222 3333",
        "private_sentinel",
        "SECRET_SENTINEL",
    ]
    with connect_database(tmp_path / "api.sqlite3") as connection:
        persisted = "\n".join(connection.iterdump())
    for value in sentinels:
        assert value not in detail.text + exported.text + persisted + caplog.text
    assert all("payload_fingerprint" not in json.dumps(item) for item in response["jobs"])
    assert DISCLAIMER in detail.text


def test_corrupt_image_explicit_failure_and_bounded_retry(client):
    record = upload(
        "",
        source_kind="screenshot",
        image_format="png",
        image_base64=base64.b64encode(b"PRIVATE-PIXEL-SENTINEL").decode(),
    )
    _, response = submit(client, [record])
    job_id = response["jobs"][0]["job_id"]
    job = wait_job(client, job_id)
    assert job["status"] == "failed" and job["result_id"] is None
    assert (
        client.post(f"/jobs/{job_id}/retry", json={"request_id": str(uuid4())}).status_code == 409
    )
    for attempt in (2, 3):
        body = {"request_id": str(uuid4()), "record": record}
        response = client.post(f"/jobs/{job_id}/retry", json=body)
        assert response.status_code == 202, response.text
        job = wait_job(client, job_id)
        assert job["attempts"] == attempt and job["result_id"] is None
        assert client.post(f"/jobs/{job_id}/retry", json=body).status_code == 202
    assert (
        client.post(
            f"/jobs/{job_id}/retry", json={"request_id": str(uuid4()), "record": record}
        ).json()["error_code"]
        == "retry_exhausted"
    )
    assert client.get("/summary").json()["statuses"] == {"failed": 1}


def test_preparation_recovery_and_raw_interruption_are_distinct(config, tmp_path):
    database = tmp_path / "recover.sqlite3"
    service = ProcessingService(database, config, MODELS)
    work = UploadRecord.model_validate(upload()).work()
    response = service.ingest(uuid4(), fingerprint("prepared"), [work])
    job = service.claim()
    service._prepared(job)
    service.close()
    other = UploadRecord.model_validate(upload("Ещё не подготовлен")).work()
    with TestClient(create_app(CONFIG, database)) as client:
        recovered = wait_job(client, response["job_ids"][0])
        assert recovered["status"] == "completed" and recovered["attempts"] == 2
    service = ProcessingService(database, config, MODELS)
    response = service.ingest(uuid4(), fingerprint("raw"), [other])
    service.claim()
    service.close()
    with TestClient(create_app(CONFIG, database)) as client:
        lost = wait_job(client, response["job_ids"][0])
        assert lost["status"] == "incomplete" and lost["error_code"] == "interrupted"
        retried = client.post(
            f"/jobs/{lost['job_id']}/retry",
            json={"request_id": str(uuid4()), "record": upload("Ещё не подготовлен")},
        )
        assert retried.status_code == 202, retried.text
        assert wait_job(client, lost["job_id"])["status"] == "completed"


def test_single_owner_and_capacity_admission(config, tmp_path):
    from safenet.config import DetectorConfig

    data = config.model_dump()
    data["limits"].update(max_pending_jobs=1, batch_size=1)
    small = DetectorConfig.model_validate(data)
    service = ProcessingService(tmp_path / "capacity.sqlite3", small, MODELS)
    try:
        with pytest.raises(RuntimeError, match="owned"):
            ProcessingService(service.database, small, MODELS)
        work = UploadRecord.model_validate(upload()).work()
        request = uuid4()
        response = service.ingest(request, fingerprint("one"), [work])
        assert service.ingest(request, fingerprint("one"), [work]) == response
        with pytest.raises(ServiceError) as error:
            service.ingest(
                uuid4(), fingerprint("two"), [UploadRecord.model_validate(upload()).work()]
            )
        assert error.value.status == 429
    finally:
        service.close()


def test_final_result_and_graph_rollback_together(client, tmp_path):
    with connect_database(tmp_path / "api.sqlite3") as connection:
        connection.execute(
            "CREATE TRIGGER injected_failure BEFORE UPDATE OF status ON jobs "
            "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'PRIVATE-SENTINEL'); END"
        )
    _, response = submit(client)
    job = wait_job(client, response["jobs"][0]["job_id"])
    assert job["status"] == "failed" and job["error_code"] == "processing_failed"
    with connect_database(tmp_path / "api.sqlite3") as connection:
        assert connection.execute("SELECT count(*) FROM analysis_results").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM graph_events").fetchone()[0] == 0
        connection.execute("DROP TRIGGER injected_failure")
    assert client.get("/summary").json()["graph"]["snapshot"]["sequence"] == 0
    retry = client.post(f"/jobs/{job['job_id']}/retry", json={"request_id": str(uuid4())})
    assert retry.status_code == 202, retry.text
    assert wait_job(client, job["job_id"])["status"] == "completed"


def test_invalid_payloads_never_echo_input(client):
    for body in (
        {"request_id": str(uuid4()), "records": [{**upload(), "label": "PRIVATE-SENTINEL"}]},
        {
            "request_id": str(uuid4()),
            "records": [
                upload(
                    "",
                    source_kind="screenshot",
                    image_format="png",
                    image_base64="PRIVATE-SENTINEL",
                )
            ],
        },
    ):
        response = client.post("/ingestions", json=body)
        assert response.status_code == 422 and "PRIVATE-SENTINEL" not in response.text
    assert client.post("/ingestions", content=b"x" * (5 * 1024 * 1024 + 1)).status_code == 413
    assert (
        client.post(
            "/ingestions", content=(chunk for chunk in (b"x" * 3_000_000, b"y" * 3_000_000))
        ).status_code
        == 413
    )


def test_migrates_foundation_data_without_losing_case_or_decisions(config, tmp_path):
    import sqlite3

    from safenet.pipeline import analyze
    from safenet.preprocessing import preprocess_record
    from safenet.storage import SCHEMA

    database = tmp_path / "legacy.sqlite3"
    record = IncomingRecord(provenance="synthetic", synthetic=True, text=HIGH)
    prepared = preprocess_record(record, config)
    result = analyze(prepared, config).result
    job_id, decision_id, request_id = str(uuid4()), str(uuid4()), str(uuid4())
    stamp = record.created_at.isoformat()
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA + "PRAGMA user_version=1;")
        connection.execute(
            "INSERT INTO cases VALUES (?, 'synthetic',1,?,'ru','text',NULL,?)",
            (str(record.case_id), prepared.content.text, stamp),
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,'completed',1,NULL,?,?)",
            (job_id, str(uuid4()), "a" * 64, str(record.case_id), stamp, stamp),
        )
        connection.execute(
            "INSERT INTO analysis_results VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(result.result_id),
                str(record.case_id),
                job_id,
                1,
                config.fingerprint,
                "0.1.0",
                None,
                "[]",
                result.model_dump_json(),
                stamp,
            ),
        )
        connection.execute(
            "INSERT INTO analyst_decisions VALUES (?,?,?,?,?,?)",
            (
                decision_id,
                request_id,
                str(record.case_id),
                str(result.result_id),
                "confirm_signs",
                stamp,
            ),
        )
    with TestClient(create_app(CONFIG, database)) as client:
        assert client.get("/health").json()["schema_version"] == 2
        assert client.get("/cases").json()["items"][0]["case_id"] == str(record.case_id)
        assert (
            client.get(f"/cases/{record.case_id}/decisions").json()["items"][0]["decision_id"]
            == decision_id
        )
        assert client.get(f"/cases/{record.case_id}/results/1").json() == result.model_dump(
            mode="json"
        )
        assert (
            client.post(
                f"/cases/{record.case_id}/reanalyze", json={"request_id": str(uuid4())}
            ).json()["error_code"]
            == "source_required"
        )


def test_queue_query_is_indexed_and_does_not_load_evidence(client, tmp_path):
    from safenet.views import CASE_SELECT

    _, response = submit(client, [upload(HIGH), upload(MEDIUM)])
    for item in response["jobs"]:
        wait_job(client, item["job_id"])
    with connect_database(tmp_path / "api.sqlite3") as connection:
        plan = [
            tuple(row)
            for row in connection.execute(
                "EXPLAIN QUERY PLAN "
                + CASE_SELECT
                + "ORDER BY coalesce(q.queue_score,-1) DESC,q.case_id LIMIT 50"
            )
        ]
    assert any("case_queue" in str(row) for row in plan)
    assert all("TEMP B-TREE" not in str(row) for row in plan)
    assert "prepared_json" not in CASE_SELECT and "result_json" not in CASE_SELECT


def test_processing_does_not_hold_a_write_transaction(client, tmp_path, monkeypatch):
    import safenet.service as module

    original = module.preprocess_record

    def checked(*args, **kwargs):
        with connect_database(tmp_path / "api.sqlite3") as connection:
            connection.execute("BEGIN IMMEDIATE")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "preprocess_record", checked)
    _, response = submit(client)
    assert wait_job(client, response["jobs"][0]["job_id"])["status"] == "completed"


@pytest.mark.parametrize("bound", ["jobs", "bytes"])
def test_http_capacity_is_explicit_and_health_stays_available(bound, tmp_path, monkeypatch):
    import threading

    import safenet.service as module

    text = CONFIG.read_text(encoding="utf-8")
    if bound == "jobs":
        text = text.replace("max_pending_jobs = 1000", "max_pending_jobs = 1").replace(
            "batch_size = 100", "batch_size = 1"
        )
    else:
        text = text.replace("max_pending_bytes = 33554432", "max_pending_bytes = 64")
    config_path = tmp_path / "small.toml"
    config_path.write_text(text, encoding="utf-8")
    entered, released = threading.Event(), threading.Event()
    original = module.preprocess_record

    def blocked(*args, **kwargs):
        entered.set()
        assert released.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "preprocess_record", blocked)
    try:
        with TestClient(create_app(config_path, tmp_path / "small.sqlite3")) as client:
            if bound == "jobs":
                _, response = submit(client)
                assert entered.wait(3)
            response = client.post(
                "/ingestions", json={"request_id": str(uuid4()), "records": [upload()]}
            )
            assert (
                response.status_code == 429 and response.json()["error_code"] == "capacity_exceeded"
            )
            assert client.get("/health").status_code == 200
            released.set()
    finally:
        released.set()


def test_superseded_failed_job_cannot_replace_a_newer_result(client, tmp_path):
    with connect_database(tmp_path / "api.sqlite3") as connection:
        connection.execute(
            "CREATE TRIGGER first_failure BEFORE UPDATE OF status ON jobs "
            "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
    _, response = submit(client)
    first = wait_job(client, response["jobs"][0]["job_id"])
    assert first["status"] == "failed"
    with connect_database(tmp_path / "api.sqlite3") as connection:
        connection.execute("DROP TRIGGER first_failure")
    second = client.post(f"/cases/{first['case_id']}/reanalyze", json={"request_id": str(uuid4())})
    assert wait_job(client, second.json()["jobs"][0]["job_id"])["version"] == 2
    retry = client.post(f"/jobs/{first['job_id']}/retry", json={"request_id": str(uuid4())})
    assert retry.status_code == 409 and retry.json()["error_code"] == "job_superseded"
    assert client.get("/cases/" + first["case_id"]).json()["latest_result"]["version"] == 2


def test_new_config_keeps_old_history_and_graph_context_separate(tmp_path):
    database = tmp_path / "configs.sqlite3"
    with TestClient(create_app(CONFIG, database)) as client:
        _, response = submit(client)
        job = wait_job(client, response["jobs"][0]["job_id"])
        case_id = job["case_id"]
        original = client.get(f"/cases/{case_id}/results/1").json()
        original_graph = client.get(f"/cases/{case_id}/graph").json()
    config = tmp_path / "changed.toml"
    config.write_text(
        CONFIG.read_text(encoding="utf-8").replace("high = 70.0", "high = 71.0"), encoding="utf-8"
    )
    with TestClient(create_app(config, database)) as client:
        assert client.get("/summary").json()["graph"]["snapshot"]["sequence"] == 0
        assert (
            client.get(
                f"/cases/{case_id}/graph", params={"result_id": original["result_id"]}
            ).json()
            == original_graph
        )
        response = client.post(f"/cases/{case_id}/reanalyze", json={"request_id": str(uuid4())})
        assert wait_job(client, response.json()["jobs"][0]["job_id"])["status"] == "completed"
        current = client.get(f"/cases/{case_id}/results/2").json()
        assert current["run"]["config_fingerprint"] != original["run"]["config_fingerprint"]
        assert client.get(f"/cases/{case_id}/results/1").json() == original
        assert client.get("/summary").json()["graph"]["snapshot"]["sequence"] == 1


@pytest.mark.ocr
def test_screenshot_api_stores_reconstruction_and_reanalysis_reuses_ocr(tmp_path, monkeypatch):
    from PIL import Image

    database = tmp_path / "image.sqlite3"
    original = (ROOT / "tests/fixtures/ocr/ru.png").read_bytes()
    record = upload(
        "",
        source_kind="screenshot",
        image_format="png",
        image_base64=base64.b64encode(original).decode(),
    )
    with TestClient(create_app(CONFIG, database)) as client:
        _, response = submit(client, [record])
        job = wait_job(client, response["jobs"][0]["job_id"])
        assert job["status"] == "completed"
        case_id = job["case_id"]
        image = client.get(f"/cases/{case_id}/image")
        assert image.status_code == 200 and image.content != original
        assert image.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(image.content)) as decoded:
            assert not decoded.info
        result = client.get(f"/cases/{case_id}/results/1").json()
        features = [feature for track in result["tracks"] for feature in track["features"]]
        assert features and all(
            e["region"] is not None for feature in features for e in feature["evidence"]
        )

    def forbidden(*args, **kwargs):
        pytest.fail("reanalysis must reuse cached OCR")

    monkeypatch.setattr("safenet.ocr.OCRProcessor.recognize", forbidden)
    with TestClient(create_app(CONFIG, database)) as client:
        response = client.post(f"/cases/{case_id}/reanalyze", json={"request_id": str(uuid4())})
        job = wait_job(client, response.json()["jobs"][0]["job_id"])
        assert job["status"] == "completed" and job["version"] == 2
        assert client.get(f"/cases/{case_id}/image").content == image.content


def test_sqlite_contention_returns_sanitized_capacity_response(client, tmp_path):
    with connect_database(tmp_path / "api.sqlite3") as locked:
        locked.execute("BEGIN IMMEDIATE")
        response = client.post(
            "/ingestions", json={"request_id": str(uuid4()), "records": [upload()]}
        )
        assert (
            response.status_code == 503 and response.json()["error_code"] == "database_unavailable"
        )
        assert response.headers["retry-after"] == "1"
    _, response = submit(client)
    assert wait_job(client, response["jobs"][0]["job_id"])["status"] == "completed"


def test_request_retry_races_are_deduplicated(client):
    from concurrent.futures import ThreadPoolExecutor

    body = {"request_id": str(uuid4()), "records": [upload()]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.post("/ingestions", json=body), range(4)))
    assert all(response.status_code == 202 for response in responses)
    ids = {response.json()["jobs"][0]["job_id"] for response in responses}
    assert len(ids) == 1
    assert wait_job(client, ids.pop())["status"] == "completed"
    assert client.get("/summary").json()["cases"] == 1


def test_history_pagination_filter_binding_and_export_completion(client, monkeypatch):
    _, response = submit(client, [upload(HIGH), upload(MEDIUM)])
    jobs = [wait_job(client, row["job_id"]) for row in response["jobs"]]
    case_id = jobs[0]["case_id"]
    result_id = client.get(f"/cases/{case_id}/results/1").json()["result_id"]
    monkeypatch.setattr("safenet.service.now", lambda: "2030-01-01T00:00:00+00:00")
    for action in ("confirm_signs", "request_review", "false_positive"):
        assert (
            client.post(
                f"/cases/{case_id}/decisions",
                json={"request_id": str(uuid4()), "result_id": result_id, "action": action},
            ).status_code
            == 200
        )
    page = client.get(f"/cases/{case_id}/decisions?limit=1").json()
    actions = [page["items"][0]["action"]]
    while page["next_cursor"]:
        page = client.get(
            f"/cases/{case_id}/decisions", params={"limit": 1, "cursor": page["next_cursor"]}
        ).json()
        actions += [row["action"] for row in page["items"]]
    assert actions == ["confirm_signs", "request_review", "false_positive"]
    stamps = [
        item["created_at"] for item in client.get(f"/cases/{case_id}/decisions").json()["items"]
    ]
    assert stamps == [
        "2030-01-01T00:00:00+00:00",
        "2030-01-01T00:00:00.000001+00:00",
        "2030-01-01T00:00:00.000002+00:00",
    ]
    assert client.get("/cases?action=false_positive").json()["items"][0]["case_id"] == case_id
    cursor = client.get("/cases?limit=1").json()["next_cursor"]
    assert (
        client.get("/cases", params={"limit": 1, "cursor": cursor, "level": "high"}).status_code
        == 422
    )
    exported = [json.loads(line) for line in client.get("/exports").text.splitlines()]
    assert exported[0]["record_type"] == "begin"
    assert exported[-1] == {
        "record_type": "end",
        "complete": True,
        "counts": {"case": 2, "result": 2, "decision": 3, "job": 2},
        "disclaimer": DISCLAIMER,
    }


def test_image_only_unreadable_input_is_incomplete(client):
    from PIL import Image

    data = io.BytesIO()
    Image.new("RGB", (200, 150), "white").save(data, format="PNG")
    _, response = submit(
        client,
        [
            upload(
                "",
                source_kind="screenshot",
                image_format="png",
                image_base64=base64.b64encode(data.getvalue()).decode(),
            )
        ],
    )
    job = wait_job(client, response["jobs"][0]["job_id"])
    assert job["status"] == "incomplete" and job["error_code"] == "ocr_failed"
    detail = client.get("/cases/" + job["case_id"]).json()
    assert detail["latest_result"] is None and detail["case"]["queue_score"] is None


@pytest.mark.parametrize("stage", ["raw", "prepared", "during_commit", "completed"])
def test_hard_process_crash_recovery(stage, tmp_path):
    import subprocess
    import sys

    database, marker = tmp_path / "crash.sqlite3", tmp_path / "ready.json"
    script = """
import json,sys,threading
from pathlib import Path
from uuid import uuid4
from contextlib import contextmanager
from safenet.config import load_config
from safenet.http_contracts import UploadRecord,fingerprint
import safenet.service as module
from safenet.service import ProcessingService
from safenet.storage import connect_database
database,marker,stage=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3]
service=ProcessingService(database,load_config(Path('config/detector.toml')),Path('.tools/tessdata'))
work=UploadRecord(provenance='synthetic',synthetic=True,
    text='Гарантированный доход. Пригласи друзей. https://a.test/').work()
response=service.ingest(uuid4(),fingerprint('crash'),[work])
job=service.claim()
def ready():
    marker.write_text(json.dumps({'job_id':job['job_id'],'case_id':job['case_id']}))
    threading.Event().wait()
if stage=='during_commit':
    original=module.connect_database
    @contextmanager
    def hooked(*args,**kwargs):
        with original(*args,**kwargs) as connection:
            connection.create_function('crash_point',0,ready)
            yield connection
    module.connect_database=hooked
    with connect_database(database) as connection:
        connection.execute("CREATE TRIGGER crash_trigger BEFORE UPDATE OF status ON jobs "
            "WHEN NEW.status='completed' BEGIN SELECT crash_point(); END")
    service._process(job)
elif stage=='prepared':
    service._prepared(job)
elif stage=='completed':
    service._process(job)
ready()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(database), str(marker), stage],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not marker.exists():
            process.kill()
            _, error = process.communicate(timeout=5)
            pytest.fail("crash fixture not ready: " + error.decode(errors="replace"))
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stderr.close()
    identifiers = json.loads(marker.read_text())
    with connect_database(database) as connection:
        if stage == "during_commit":
            assert connection.execute("SELECT count(*) FROM analysis_results").fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM graph_events").fetchone()[0] == 0
            connection.execute("DROP TRIGGER crash_trigger")
    with TestClient(create_app(CONFIG, database)) as client:
        job = wait_job(client, identifiers["job_id"])
        assert job["status"] == ("incomplete" if stage == "raw" else "completed")
        if stage == "raw":
            assert job["error_code"] == "interrupted" and job["result_id"] is None
        else:
            assert job["attempts"] == (1 if stage == "completed" else 2)
            assert len(client.get(f"/cases/{job['case_id']}/results").json()["items"]) == 1
            assert client.get("/summary").json()["graph"]["snapshot"]["sequence"] == 1
