from pathlib import Path

from meemee.auth import TokenStore
from meemee.companion.models import UserProfile
from meemee.companion.store import CompanionStore


def test_export_then_delete_companion_and_disable_account(tmp_path: Path):
    tokens=TokenStore(tmp_path/'auth.db'); account,session=tokens.create_account('delete@example.com','correct horse battery','Delete')
    store=CompanionStore(tmp_path/'companion.db'); store.upsert_user(UserProfile(user_id=account['id'],display_name='Delete'))
    exported=store.export_user_data(account['id']); assert exported['profile']['display_name']=='Delete'
    counts=store.delete_user_data(account['id']); assert counts['profiles']==1
    assert store.export_user_data(account['id'])['profile'] is None
    assert tokens.disable_account(account['id']) and tokens.authenticate(session) is None
    assert tokens.login_account('delete@example.com','correct horse battery') is None
