from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import Database, MigrationStore
from .cutover import SPECS, Cutover


def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="meemee-pg",description="Offline SQLite to PostgreSQL cutover tools")
    p.add_argument("--dsn",default=os.getenv("MEEMEE_POSTGRES_DSN"),help="PostgreSQL DSN (prefer MEEMEE_POSTGRES_DSN to avoid shell history)")
    for name in SPECS:p.add_argument(f"--{name}",type=Path,required=True,help=f"path to {name} SQLite database")
    sub=p.add_subparsers(dest="command",required=True)
    copy=sub.add_parser("copy",help="copy into empty migrated PostgreSQL tables")
    copy.add_argument("--batch-size",type=int,default=1000)
    sub.add_parser("verify",help="compare canonical row counts and SHA-256 digests")
    dual=sub.add_parser("dual-verify",help="repeat verification during an operator-controlled observation window")
    dual.add_argument("--cycles",type=int,default=3);dual.add_argument("--interval-seconds",type=float,default=10)
    return p

def validate_sqlite(sources:dict[str,Path])->None:
    for group,path in sources.items():
        if not path.is_file():raise RuntimeError(f"{group} source does not exist: {path}")
        connection=sqlite3.connect(f"file:{path.resolve()}?mode=ro",uri=True)
        try:
            result=connection.execute("PRAGMA integrity_check").fetchone()[0]
            if result!="ok":raise RuntimeError(f"{group} SQLite integrity check failed: {result}")
            tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            needed={spec.source for spec in SPECS[group]}
            if missing:=needed-tables:raise RuntimeError(f"{group} source is missing tables: {sorted(missing)}")
        finally:connection.close()

def main(argv:list[str]|None=None)->int:
    args=parser().parse_args(argv)
    if not args.dsn:raise SystemExit("PostgreSQL DSN required via --dsn or MEEMEE_POSTGRES_DSN")
    sources={name:getattr(args,name) for name in SPECS};validate_sqlite(sources)
    db=Database(args.dsn)
    try:
        MigrationStore(db).apply();cutover=Cutover(db,sources)
        if args.command=="copy":output={"copied":cutover.copy(batch_size=args.batch_size),"verification":cutover.verify()}
        elif args.command=="verify":output={"verification":cutover.verify()}
        else:output={"cycles":cutover.dual_verify(cycles=args.cycles,interval_seconds=args.interval_seconds)}
        print(json.dumps(output,indent=2,sort_keys=True,default=str))
        checks=output.get("verification") or output["cycles"][-1]
        return 0 if all(item["match"] for item in checks.values()) else 2
    finally:db.close()
if __name__=="__main__":sys.exit(main())
