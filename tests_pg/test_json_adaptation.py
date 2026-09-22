from pathlib import Path


def test_every_runtime_jsonb_write_uses_psycopg_jsonb_adapter():
    root = Path('meemee_persist_pg')
    memory=(root/'memory.py').read_text(); jobs=(root/'jobs.py').read_text(); audit=(root/'audit.py').read_text()
    assert 'Jsonb(metadata or {})' in memory
    assert 'Jsonb(payload)' in jobs and 'Jsonb(result)' in jobs
    assert 'Jsonb(metadata or {})' in audit


def test_every_enum_case_branch_is_explicitly_cast():
    source=Path('meemee_persist_pg/jobs.py').read_text()
    assert "THEN 'queued'::meemee_job_status ELSE 'failed'::meemee_job_status END" in source
    assert "THEN 'queued' ELSE 'failed' END" not in source
