"""Compare SQLite and PostgreSQL semantic/hybrid search on the same corpus (real local PostgreSQL via pgserver)."""
import tempfile, warnings, pathlib
warnings.filterwarnings("ignore")
import pgserver
from meemee.memory import MemoryStore as Lite
from meemee_persist_pg import Database, MigrationStore
from meemee_persist_pg.memory import MemoryStore as PG

CORPUS = ["postgres database backup and restore procedure", "restore the database from a nightly dump",
          "browser login cookies and session", "database restore drill checklist", "quarterly roadmap planning notes"]
QUERIES = ["database restore", "database backup restore dump", "zzz unrelated"]
def main():
    d = pathlib.Path(tempfile.mkdtemp()); srv = pgserver.get_server(str(d / "pg"))
    db = Database(srv.get_uri()); MigrationStore(db).apply()
    pg = PG(db); lite = Lite(d / "m.db")
    for c in CORPUS: pg.add("r", "fact", c); lite.add("r", "fact", c)
    for q in QUERIES:
        for name in ("semantic_search", "hybrid_search"):
            a = [r["id"] for r in getattr(lite, name)(q, 5)]; b = [r["id"] for r in getattr(pg, name)(q, 5)]
            print(f"{name:16} {q!r:34} sqlite={a} pg={b} {'SAME' if a == b else 'DIFFERENT'}")
    db.close()
main()
