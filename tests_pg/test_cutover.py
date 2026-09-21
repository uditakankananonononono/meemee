import hashlib,json,sqlite3
from meemee_persist_pg.cutover import TableSpec,canonical,digest_rows,transform

def row(values):
    db=sqlite3.connect(":memory:");db.row_factory=sqlite3.Row
    fields=",".join(f"'{v}' AS {k}" for k,v in values.items())
    return db.execute(f"SELECT {fields}").fetchone()

def test_memory_transform_and_canonical_digest():
    spec=TableSpec("memories","meemee_memories",("id","run_id","kind","content","metadata","created_at"),("id",))
    changed=transform(spec,row({"id":"1","run_id":"r","kind":"fact","content":"x","metadata":'{"b":2,"a":1}',"created_at":"2026-01-01T00:00:00+00:00"}))
    assert changed[4]=={"a":1,"b":2}
    count,first=digest_rows([dict(zip(spec.columns,changed))]);count2,second=digest_rows([dict(zip(spec.columns,changed))])
    assert count==count2==1 and first==second and len(first)==64

def test_token_transform_preserves_digest_and_sorts_nothing():
    spec=TableSpec("api_tokens","meemee_api_tokens",("id","name","digest","scopes","created_at","last_used_at","expires_at","revoked_at"),("id",))
    db=sqlite3.connect(":memory:");db.row_factory=sqlite3.Row
    value=db.execute("SELECT 'i' id,'n' name,? digest,'z a' scopes,'2026-01-01T00:00:00+00:00' created_at,NULL last_used_at,NULL expires_at,NULL revoked_at",(b'abc',)).fetchone()
    result=transform(spec,value);assert result[2]==b'abc' and result[3]==['z','a']
