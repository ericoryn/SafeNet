import sqlite3

import pytest

from safenet.storage import SCHEMA_VERSION, connect_database, initialize_database


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "safenet.sqlite3"
    initialize_database(path)
    return path


def insert_case(connection, case_id="case-a"):
    connection.execute(
        "INSERT INTO cases VALUES (?, 'synthetic', 1, 'Synthetic fixture', 'ru', 'text', NULL, ?)",
        (case_id, "2026-10-24T00:00:00Z"),
    )


def insert_result(connection, *, result_id="result-a", version=1, job_id="job-a"):
    connection.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, 'case-a', 'processing', 1, NULL, ?, ?)",
        (job_id, job_id, "a" * 64, "now", "now"),
    )
    connection.execute(
        "INSERT INTO analysis_results VALUES (?, 'case-a', ?, ?, ?, 'test', NULL, '[]', '{}', ?)",
        (result_id, job_id, version, "a" * 64, "now"),
    )
    connection.execute("UPDATE jobs SET status='completed' WHERE job_id=?", (job_id,))


def test_schema_initialization_is_repeatable_and_enables_foreign_keys(database):
    initialize_database(database)
    with connect_database(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM cases").fetchone()[0] == 0


def test_unknown_schema_is_not_overwritten(database):
    with connect_database(database) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="migration required"):
        initialize_database(database)
    with connect_database(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_missing_database_is_not_silently_recreated(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(sqlite3.OperationalError), connect_database(path):
        pass
    assert not path.exists()


def test_invalid_records_and_missing_parents_are_rejected(database):
    with connect_database(database) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO cases VALUES ('bad','synthetic',0,'fixture','ru','text',NULL,'now')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO jobs VALUES ('j','r',?,'missing','queued',0,NULL,'now','now')",
                ("a" * 64,),
            )


def test_decisions_append_only_and_request_ids_unique(database):
    with connect_database(database) as connection:
        insert_case(connection)
        insert_result(connection)
        for index, action in enumerate(("confirm_signs", "request_review")):
            connection.execute(
                "INSERT INTO analyst_decisions VALUES (?, ?, 'case-a', 'result-a', ?, 'now')",
                (f"decision-{index}", f"request-{index}", action),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO analyst_decisions VALUES ('duplicate','request-0',"
                "'case-a','result-a','false_positive','now')"
            )
        for statement in (
            "UPDATE analyst_decisions SET action='false_positive'",
            "DELETE FROM analyst_decisions",
            "UPDATE analysis_results SET code_version='changed'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
        assert connection.execute("SELECT count(*) FROM analyst_decisions").fetchone()[0] == 2


def test_versioned_results_and_cross_case_references(database):
    with connect_database(database) as connection:
        insert_case(connection)
        insert_case(connection, "case-b")
        insert_result(connection)
        insert_result(connection, result_id="result-b", version=2, job_id="job-b")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO analyst_decisions VALUES ('d','r','case-b','result-a',"
                "'confirm_signs','now')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            insert_result(connection, result_id="result-c", version=2, job_id="job-c")
        assert connection.execute("SELECT count(*) FROM analysis_results").fetchone()[0] == 2


def test_transaction_rolls_back_on_failure(database):
    with pytest.raises(RuntimeError), connect_database(database) as connection:
        insert_case(connection)
        raise RuntimeError("injected interruption")
    with connect_database(database) as connection:
        assert connection.execute("SELECT count(*) FROM cases").fetchone()[0] == 0
