"""Pure supplied-record inventory; does not access or verify backup files."""
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class ManifestInventory:
    records: tuple[tuple[str,int,str], ...]
    total_files: int
    total_bytes: int

def manifest_inventory(records, *, max_files=1000, max_bytes=1000000000):
    for cap in (max_files,max_bytes):
        if type(cap) is not int or cap<=0:raise ValueError('positive exact integer caps required')
    if type(records) is not list:raise TypeError('exact list required')
    if len(records)>max_files:raise ValueError('file cap exceeded')
    out=[];seen=set();total=0
    for row in records:
        if type(row) is not dict or set(row)!={'name','bytes','sha256'}:raise TypeError('exact record fields required')
        name,size,digest=row['name'],row['bytes'],row['sha256']
        if type(name) is not str or not name or name in {'.','..'} or '/' in name or '\\' in name or '\x00' in name or not name.endswith('.sqlite3'):raise ValueError('safe sqlite3 component required')
        if name in seen:raise ValueError('duplicate name')
        if type(size) is not int or size<0:raise ValueError('nonnegative exact integer bytes required')
        if type(digest) is not str or re.fullmatch('[0-9a-f]{64}',digest) is None:raise ValueError('lowercase sha256 required')
        total+=size
        if total>max_bytes:raise ValueError('byte cap exceeded')
        seen.add(name);out.append((name,size,digest))
    return ManifestInventory(tuple(sorted(out)),len(out),total)
