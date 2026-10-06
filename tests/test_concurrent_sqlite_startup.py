import subprocess
import sys


def test_six_processes_can_initialize_memory_and_job_schema_together(tmp_path):
    script = """
import sys
from pathlib import Path
from meemee.jobs import JobStore
from meemee.memory import MemoryStore
from meemee.semantic_memory import HashingEmbedder
root = Path(sys.argv[1])
for _ in range(5):
    store = JobStore(root / 'jobs.sqlite3')
    assert store.enqueue('work', principal='owner')
    memory = MemoryStore(root / 'memory.sqlite3', HashingEmbedder())
    assert memory.add('r','fact','startup concurrency', owner_id="default")
    memory.connection.close()
    store.db.close()
"""
    workers = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(6)]
    for worker in workers:
        _out, err = worker.communicate(timeout=20)
        assert worker.returncode == 0, err.decode()
