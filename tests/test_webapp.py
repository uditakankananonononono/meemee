from fastapi.testclient import TestClient

from meemee.api import app

client = TestClient(app)

def test_customer_app_is_packaged_and_mounted():
    page = client.get('/app/')
    assert page.status_code == 200
    assert 'A companion that remembers with receipts.' in page.text
    assert 'operator' not in page.text.lower()
    assert client.get('/app/assets/app.js').status_code == 200
    assert client.get('/app/assets/app.css').status_code == 200

def test_customer_app_has_real_onboarding_chat_and_persona_flows():
    script = client.get('/app/assets/app.js').text
    for endpoint in ('/v1/companion/users/', '/v1/companion/chat', '/persona', '/conversations'):
        assert endpoint in script
    assert "method:'PUT'" in script and "method:'POST'" in script
