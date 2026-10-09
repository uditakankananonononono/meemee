import pytest
from meemee.backup_manifest_inventory import manifest_inventory

def row(name='a.sqlite3',size=3):return {'name':name,'bytes':size,'sha256':'a'*64}
def test_sorted_counts_detached():
    rows=[row('b.sqlite3',2),row()];got=manifest_inventory(rows);rows[0]['bytes']=99
    assert got.total_files==2 and got.total_bytes==5
    assert got.records==(('a.sqlite3',3,'a'*64),('b.sqlite3',2,'a'*64))
    with pytest.raises((AttributeError,TypeError)):got.total_bytes=9

def test_empty_and_caps():
    assert manifest_inventory([]).total_files==0
    assert manifest_inventory([row(size=0)],max_bytes=1).total_bytes==0
    with pytest.raises(ValueError):manifest_inventory([row(),row('b.sqlite3')],max_files=1)
    with pytest.raises(ValueError):manifest_inventory([row()],max_bytes=2)
    with pytest.raises(ValueError):manifest_inventory([row(),row()])

@pytest.mark.parametrize('name',['../a.sqlite3','/a.sqlite3','a/b.sqlite3','a\\b.sqlite3','x.txt','', '.', '..'])
def test_name_refusal(name):
    with pytest.raises(ValueError):manifest_inventory([row(name)])

@pytest.mark.parametrize('value',[True,-1,1.0,'3'])
def test_byte_type(value):
    with pytest.raises((TypeError,ValueError)):manifest_inventory([row(size=value)])

def test_shape_digest_types():
    for value in [None,(),[None],[dict(row(),extra=1)],[dict(row(),sha256='A'*64)]]:
        with pytest.raises((TypeError,ValueError)):manifest_inventory(value)
    for cap in [True,0,-1,1.0]:
        with pytest.raises((TypeError,ValueError)):manifest_inventory([],max_files=cap)
