import json
from datetime import datetime, timezone

import httpx
import pytest
from meemee_client import MeemeeClient


def test_scoped_approval_round_trip_and_safe_paths():
    requests=[]
    now=datetime.now(timezone.utc).isoformat()
    def handler(request):
        requests.append(request)
        if request.method=="GET": return httpx.Response(200,json={"approvals":[{"principal":"team/user","tool":"github.push_branch","granted_by":"admin","granted_at":now,"expires_at":None,"revoked_at":None,"argument_constraints":{"owner":"acme"}}]})
        return httpx.Response(200,json={"ok":True})
    client=MeemeeClient("https://example.test","token",transport=httpx.MockTransport(handler))
    listed=client.approvals.list("team/user")
    assert listed[0].argument_constraints=={"owner":"acme"} and listed[0].granted_at
    client.approvals.grant("team/user","github.push_branch",argument_constraints={"owner":"acme"})
    assert json.loads(requests[-1].content)["argument_constraints"]=={"owner":"acme"}
    client.approvals.revoke("team/user","github.push_branch")
    assert requests[-1].url.path=="/v1/approvals/team/user/github.push_branch"


def test_approval_constraints_require_mapping():
    client=MeemeeClient("https://example.test","token",transport=httpx.MockTransport(lambda r:httpx.Response(200,json={})))
    with pytest.raises(TypeError): client.approvals.grant("u","tool",argument_constraints=[])  # type: ignore[arg-type]
