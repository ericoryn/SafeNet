"""Real HTTP demo with a hard restart. Uses an unused database and local port only."""

import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def run(database: Path, port: int) -> dict:
    if database.exists():
        raise FileExistsError("choose an unused database path")
    base = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "SAFENET_DB_PATH": str(database.resolve()),
        "SAFENET_CONFIG": str(ROOT / "config/detector.toml"),
        "SAFENET_TESSDATA": str(ROOT / ".tools/tessdata"),
    }
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)

    def call(path, payload=None):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request = Request(
            base + path, data=data, headers={"Content-Type": "application/json"} if data else {}
        )
        try:
            with urlopen(request, timeout=10) as response:
                body = response.read()
                return response.status, json.loads(body) if "json" in response.headers.get(
                    "Content-Type", ""
                ) and "ndjson" not in response.headers.get("Content-Type", "") else body
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def start(number):
        log = reports / f"phase6_server_{number}.log"
        with log.open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "safenet.api:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--workers",
                    "1",
                    "--limit-concurrency",
                    "32",
                    "--no-access-log",
                ],
                cwd=ROOT,
                env=environment,
                stdout=stream,
                stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("demo server failed to start; inspect its local log")
                if "Uvicorn running on" in log.read_text(encoding="utf-8", errors="replace"):
                    status, health = call("/health")
                    if status == 200 and health["detection_ready"]:
                        return process
                time.sleep(0.05)
            raise TimeoutError("demo server startup deadline")
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            raise

    def finish(job_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, response = call("/jobs/" + job_id)
            assert status == 200
            if response["job"]["status"] not in ("queued", "processing"):
                return response["job"]
            time.sleep(0.02)
        raise TimeoutError("demo job deadline")

    def text(value, **changes):
        return {"provenance": "synthetic", "synthetic": True, "text": value, **changes}

    blank = io.BytesIO()
    Image.new("RGB", (200, 150), "white").save(blank, format="PNG")
    records = [
        text(
            "ФИО: Тестов Тест Тестович; +7 000 111 22 33; 0000 1111 2222 3333; "
            "Гарантированный доход. Пригласи друзей. https://shared.test/private?ref=PRIVATE_SENTINEL"
        ),
        text("Гарантированный доход. Успей сегодня. https://shared.test/"),
        text("Взнос 5000 тг. Случайный выбор. Комиссия организатора."),
        text("Казино. Жми ссылку. Мой выигрыш! Промокод DEMO42.", source_kind="transcript"),
        text("Тегін байқау. Қатысу тегін. Жеңімпаз кездейсоқ таңдалады."),
    ]
    for image in (
        (ROOT / "tests/fixtures/ocr/ru.png").read_bytes(),
        b"PRIVATE-PIXEL-SENTINEL",
        blank.getvalue(),
    ):
        records.append(
            text(
                "",
                source_kind="screenshot",
                image_format="png",
                image_base64=base64.b64encode(image).decode(),
            )
        )
    payload = {"request_id": str(uuid4()), "records": records}
    process = start(1)
    try:
        status, submission = call("/ingestions", payload)
        assert status == 202
        jobs = [finish(row["job_id"]) for row in submission["jobs"]]
        assert Counter(row["status"] for row in jobs) == {
            "completed": 6,
            "failed": 1,
            "incomplete": 1,
        }
        case_id = jobs[0]["case_id"]
        _, original = call(f"/cases/{case_id}/results/1")
        original_id = original["result_id"]
        commands = []
        for action in ("confirm_signs", "false_positive", "request_review"):
            command = {"request_id": str(uuid4()), "result_id": original_id, "action": action}
            status, saved = call(f"/cases/{case_id}/decisions", command)
            assert status == 200
            assert call(f"/cases/{case_id}/decisions", command)[1] == saved
            commands.append(command)
        _, updated = call(f"/cases/{case_id}/reanalyze", {"request_id": str(uuid4())})
        assert finish(updated["jobs"][0]["job_id"])["version"] == 2
        assert call(f"/cases/{case_id}/results/1")[1] == original
        assert call("/ingestions", payload)[1]["jobs"][0]["job_id"] == jobs[0]["job_id"]
        assert call("/ingestions", {**payload, "records": [text("Другой материал")]})[0] == 409
        _, before = call("/summary")
        process.kill()
        process.wait(timeout=10)
        process = start(2)
        _, after = call("/summary")
        assert before == after
        assert call(f"/cases/{case_id}/results/1")[1] == original
        history = call(f"/cases/{case_id}/decisions")[1]["items"]
        assert [row["action"] for row in history] == [row["action"] for row in commands]
        assert call("/ingestions", payload)[1]["jobs"][0]["job_id"] == jobs[0]["job_id"]
        _, exported = call("/exports")
        export_text = exported.decode()
        parsed = [json.loads(line) for line in export_text.splitlines()]
        assert parsed[-1]["record_type"] == "end" and parsed[-1]["complete"]
        from safenet.storage import connect_database

        with connect_database(database) as connection:
            stored = "\n".join(connection.iterdump())
        for sentinel in (
            "Тестов",
            "+7 000 111 22 33",
            "0000 1111 2222 3333",
            "PRIVATE_SENTINEL",
            "PRIVATE-PIXEL-SENTINEL",
        ):
            assert sentinel not in export_text + stored
        (reports / "phase6_sanitized_export.jsonl").write_bytes(exported)
        report = {
            "scope": "independent synthetic HTTP smoke fixtures; no quality evaluation",
            "api_url": base,
            "database": database.relative_to(ROOT).as_posix(),
            "jobs": jobs,
            "summary": after,
            "decisions_saved": len(history),
            "historical_result_unchanged": True,
            "hard_restart_verified": True,
            "ingestion_and_decision_deduplication_verified": True,
            "conflicting_request_rejected": True,
            "privacy_sentinels_absent": True,
            "export_counts": parsed[-1]["counts"],
        }
        (reports / "phase6_api_smoke.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return report
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "data/api-demo-v1.sqlite3")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    report = run(args.database.resolve(), args.port)
    print(
        json.dumps(
            {
                "statuses": report["summary"]["statuses"],
                "decisions": report["decisions_saved"],
                "hard_restart_verified": report["hard_restart_verified"],
                "export_counts": report["export_counts"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
