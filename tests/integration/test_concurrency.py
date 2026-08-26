from __future__ import annotations

from threading import Thread

from tests.conftest import SAMPLE_CLEAN


def test_concurrent_ingest_and_analyze_is_reliable(pipeline) -> None:
    """Надёжность файлового хранилища при параллельной работе.

    Sync-эндпоинты FastAPI выполняются в threadpool, поэтому параллельные
    ingest/analyze — реальный сценарий. Каждый поток работает со своим
    документом; хранилище обязано оставаться консистентным.
    """
    content = SAMPLE_CLEAN.read_bytes()
    threads = 8
    errors: list[Exception] = []
    analysis_ids: list[str] = []
    from threading import Lock

    lock = Lock()

    def work(index: int) -> None:
        try:
            document = pipeline.ingest(f"concurrent-{index}.txt", content)
            report = pipeline.analyze(document.document_id)
            assert report.facts, "пустой отчёт в конкурентном сценарии"
            with lock:
                analysis_ids.append(report.analysis_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [Thread(target=work, args=(i,)) for i in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)

    assert not errors, errors
    assert len(analysis_ids) == threads
    # каждый отчёт читается обратно из хранилища
    for analysis_id in analysis_ids:
        assert pipeline.get_analysis(analysis_id) is not None
    # документов ровно столько, сколько загрузок
    assert len(pipeline._documents.list()) == threads


def test_concurrent_analyze_of_same_document(pipeline) -> None:
    """Несколько анализов одного документа параллельно: отчёты независимы,
    ни один не теряется и не повреждается."""
    document = pipeline.ingest(SAMPLE_CLEAN.name, SAMPLE_CLEAN.read_bytes())
    threads = 6
    errors: list[Exception] = []
    reports: list = []
    from threading import Lock

    lock = Lock()

    def work() -> None:
        try:
            report = pipeline.analyze(document.document_id)
            with lock:
                reports.append(report)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [Thread(target=work) for _ in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)

    assert not errors, errors
    assert len(reports) == threads
    # все analysis_id уникальны — отчёты не перезаписали друг друга
    ids = {report.analysis_id for report in reports}
    assert len(ids) == threads
    for report in reports:
        stored = pipeline.get_analysis(report.analysis_id)
        assert stored is not None
        assert len(stored.facts) == len(report.facts)
