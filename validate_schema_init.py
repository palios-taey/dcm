"""Red/green multiprocess validation for clean Neo4j schema initialization."""

import hashlib
import importlib.util
import json
import logging
import multiprocessing as mp
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import time

from neo4j import GraphDatabase

import mesh


PROCESS_COUNT = 10
LEGACY_SHA = "f8103302fa9ccb089b7824008e453655db91ba27"
LEGACY_MESH_SHA256 = "2c81babac876c944df129fff2cf26d610870e50696613a341d0092d6426bcb44"
EXPECTED_CONSTRAINTS = sorted(
    statement.split()[2] for statement in mesh._SCHEMA_STATEMENTS
)


class _RetryCounter(logging.Handler):
    def __init__(self):
        super().__init__()
        self.count = 0

    def emit(self, record):
        if "Transaction failed and will be retried" in record.getMessage():
            self.count += 1


def _load_mesh(module_path, process_index):
    module_name = f"dcm_schema_probe_{process_index}_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load mesh module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint_names(driver, database):
    with driver.session(database=database) as session:
        return sorted(
            row["name"]
            for row in session.run(
                "SHOW CONSTRAINTS YIELD name "
                "WHERE name IN $names RETURN name ORDER BY name",
                names=EXPECTED_CONSTRAINTS,
            )
        )


def _initialize(module_path, barrier, barrier_release_ns, results, process_index):
    retry_counter = _RetryCounter()
    neo4j_logger = logging.getLogger("neo4j")
    neo4j_logger.addHandler(retry_counter)
    loaded_mesh = None
    started_ns = None
    try:
        loaded_mesh = _load_mesh(module_path, process_index)
        barrier.wait(timeout=30)
        started_ns = time.monotonic_ns()
        with barrier_release_ns.get_lock():
            if barrier_release_ns.value == 0:
                barrier_release_ns.value = started_ns
        driver = loaded_mesh._ensure_wave_schema()
        observed_constraints = _constraint_names(driver, loaded_mesh.DCM_NEO4J_DATABASE)
        results.put(
            {
                "process_index": process_index,
                "pid": os.getpid(),
                "started_ns": started_ns,
                "barrier_release_ns": barrier_release_ns.value,
                "uri": loaded_mesh.DCM_NEO4J_URI,
                "database": loaded_mesh.DCM_NEO4J_DATABASE,
                "constraints": observed_constraints,
                "retry_count": retry_counter.count,
                "ok": observed_constraints == EXPECTED_CONSTRAINTS,
            }
        )
        driver.close()
    except BaseException as error:
        results.put(
            {
                "process_index": process_index,
                "pid": os.getpid(),
                "started_ns": started_ns,
                "barrier_release_ns": barrier_release_ns.value,
                "uri": getattr(loaded_mesh, "DCM_NEO4J_URI", None),
                "database": getattr(loaded_mesh, "DCM_NEO4J_DATABASE", None),
                "retry_count": retry_counter.count,
                "error": f"{type(error).__name__}: {str(error).splitlines()[0][:500]}",
                "ok": False,
            }
        )
    finally:
        neo4j_logger.removeHandler(retry_counter)


def _run_round(label, module_path, observer):
    initial_constraints = _constraint_names(observer, mesh.DCM_NEO4J_DATABASE)
    if initial_constraints:
        raise RuntimeError(
            f"{label} requires empty DCM constraint state; found {initial_constraints}"
        )
    context = mp.get_context("spawn")
    barrier = context.Barrier(PROCESS_COUNT)
    barrier_release_ns = context.Value("q", 0)
    results = context.Queue()
    processes = [
        context.Process(
            target=_initialize,
            args=(module_path, barrier, barrier_release_ns, results, index),
        )
        for index in range(PROCESS_COUNT)
    ]
    for process in processes:
        process.start()

    observations = []
    try:
        for _ in processes:
            observations.append(results.get(timeout=90))
    except queue.Empty:
        observations.append({"ok": False, "error": "worker receipt timeout"})
    finally:
        for process in processes:
            process.join(timeout=10)
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    final_constraints = _constraint_names(observer, mesh.DCM_NEO4J_DATABASE)
    with observer.session(database=mesh.DCM_NEO4J_DATABASE) as session:
        for constraint_name in EXPECTED_CONSTRAINTS:
            session.run(f"DROP CONSTRAINT {constraint_name} IF EXISTS").consume()
    cleanup_constraints = _constraint_names(observer, mesh.DCM_NEO4J_DATABASE)
    exit_codes = [process.exitcode for process in processes]
    pids = {item.get("pid") for item in observations if item.get("pid")}
    start_times = [item["started_ns"] for item in observations if "started_ns" in item]
    start_spread_ms = (
        (max(start_times) - min(start_times)) / 1_000_000 if start_times else None
    )
    worker_errors = [item.get("error") for item in observations if item.get("error")]
    return {
        "label": label,
        "process_count": PROCESS_COUNT,
        "distinct_pids": len(pids),
        "pids": sorted(pids),
        "barrier_release_ns": barrier_release_ns.value,
        "start_spread_ms": start_spread_ms,
        "same_uri": len({item.get("uri") for item in observations if item.get("uri")})
        == 1,
        "same_database": len(
            {item.get("database") for item in observations if item.get("database")}
        )
        == 1,
        "initial_constraints": initial_constraints,
        "final_constraints": final_constraints,
        "cleanup_constraints": cleanup_constraints,
        "retry_count": sum(item.get("retry_count", 0) for item in observations),
        "exit_codes": exit_codes,
        "worker_errors": worker_errors,
        "passed": (
            len(observations) == PROCESS_COUNT
            and all(item.get("ok") for item in observations)
            and all(code == 0 for code in exit_codes)
            and len(pids) == PROCESS_COUNT
            and start_spread_ms is not None
            and start_spread_ms <= 2_000
            and final_constraints == EXPECTED_CONSTRAINTS
            and not cleanup_constraints
        ),
    }


def main():
    mesh._require_safe_uri(mesh.DCM_NEO4J_URI)
    observer = GraphDatabase.driver(mesh.DCM_NEO4J_URI, auth=mesh._AUTH)
    observer.verify_connectivity()
    legacy_source = subprocess.run(
        ["git", "show", f"{LEGACY_SHA}:mesh.py"],
        check=True,
        capture_output=True,
    ).stdout
    observed_legacy_sha256 = hashlib.sha256(legacy_source).hexdigest()
    if observed_legacy_sha256 != LEGACY_MESH_SHA256:
        raise RuntimeError(
            "unfixed control source digest mismatch: "
            f"expected {LEGACY_MESH_SHA256}, observed {observed_legacy_sha256}"
        )

    with tempfile.TemporaryDirectory(prefix="dcm-schema-red-green-") as directory:
        legacy_path = Path(directory) / "mesh_unfixed.py"
        legacy_path.write_bytes(legacy_source)
        legacy = _run_round("unfixed-red", str(legacy_path), observer)
        candidate = _run_round("candidate-green", str(Path(mesh.__file__)), observer)
    observer.close()

    legacy_has_expected_failure = (
        not legacy["passed"]
        and legacy["distinct_pids"] == PROCESS_COUNT
        and legacy["barrier_release_ns"] != 0
        and legacy["start_spread_ms"] is not None
        and legacy["start_spread_ms"] <= 2_000
        and legacy["same_uri"]
        and legacy["same_database"]
        and any(
            "TransientError" in error or "Deadlock" in error
            for error in legacy["worker_errors"]
        )
        and not legacy["initial_constraints"]
        and not legacy["cleanup_constraints"]
    )
    candidate_has_expected_success = (
        candidate["passed"]
        and not candidate["initial_constraints"]
        and not candidate["cleanup_constraints"]
    )
    evidence = {
        "legacy_sha": LEGACY_SHA,
        "legacy_mesh_sha256": observed_legacy_sha256,
        "expected_constraints": EXPECTED_CONSTRAINTS,
        "red": legacy,
        "green": candidate,
        "red_discriminates": legacy_has_expected_failure,
        "green_accepts": candidate_has_expected_success,
    }
    print(json.dumps(evidence, sort_keys=True))
    if not legacy_has_expected_failure or not candidate_has_expected_success:
        raise SystemExit("DCM MULTIPROCESS SCHEMA RED/GREEN VALIDATION: FAIL")
    print("DCM MULTIPROCESS SCHEMA RED/GREEN VALIDATION: PASS")


if __name__ == "__main__":
    main()
