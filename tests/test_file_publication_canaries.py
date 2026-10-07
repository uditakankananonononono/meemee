"""Failed workspace writes must not publish partial user content."""
from pathlib import Path

import pytest

from meemee.tools.filesystem import WriteArgs, WriteFile


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', [True, False])
async def test_failed_write_preserves_destination(tmp_path, monkeypatch, existing):
    target = tmp_path / 'note.txt'
    if existing:
        target.write_text('old complete contents')
    original = Path.write_text
    def partial_then_fail(path, data, *args, **kwargs):
        original(path, data[:4], *args, **kwargs)
        raise OSError('simulated full disk')
    monkeypatch.setattr(Path, 'write_text', partial_then_fail)
    with pytest.raises(OSError):
        await WriteFile(tmp_path).run(WriteArgs(path='note.txt', content='new complete contents'))
    if existing:
        assert target.read_text() == 'old complete contents'
    else:
        assert not target.exists()


@pytest.mark.asyncio
async def test_successful_atomic_write_preserves_mode_and_cleans_temp(tmp_path):
    import stat
    target = tmp_path / 'note.txt'
    target.write_text('old')
    target.chmod(0o640)
    result = await WriteFile(tmp_path).run(WriteArgs(path='note.txt', content='new complete contents'))
    assert target.read_text() == 'new complete contents'
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert result['bytes'] == len('new complete contents')
    assert list(tmp_path.glob('.meemee-write-*')) == []
