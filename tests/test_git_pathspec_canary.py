"""Explicit commit paths must be literal filenames, not Git glob expressions."""
import subprocess

import pytest

from meemee.tools.git import GitCommit, GitCommitArgs


@pytest.mark.asyncio
async def test_explicit_star_filename_does_not_commit_unselected_files(tmp_path):
    def git(*args):
        return subprocess.run(['git',*args],cwd=tmp_path,check=True,capture_output=True,text=True).stdout
    git('init')
    git('config','user.name','Fixture')
    git('config','user.email','fixture@example.invalid')
    (tmp_path / 'base').write_text('base')
    git('add','base')
    git('commit','-m','base')
    (tmp_path / '*').write_text('chosen literal star')
    (tmp_path / 'private.txt').write_text('unselected')
    await GitCommit(tmp_path).run(GitCommitArgs(message='chosen star file',paths=['*']))
    assert git('show','--pretty=format:','--name-only','HEAD').strip() == '*'
    assert 'private.txt' in git('status','--short')
