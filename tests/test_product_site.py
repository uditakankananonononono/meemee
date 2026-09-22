from fastapi.testclient import TestClient

from meemee.api import app

client = TestClient(app)

def test_public_product_site_pages_are_packaged():
    landing = client.get('/product/')
    assert landing.status_code == 200
    assert 'It remembers.' in landing.text
    assert 'does not yet process payments' in landing.text
    for path, phrase in [('/product/capabilities.html','What works. What does not.'),('/product/docs.html','From zero to first conversation.')]:
        response=client.get(path); assert response.status_code==200 and phrase in response.text
    assert client.get('/product/styles.css').status_code==200

def test_product_claims_name_current_gaps_and_real_routes():
    pages=''.join(client.get(path).text for path in ('/product/','/product/capabilities.html','/product/docs.html'))
    for truth in ('Email verification','password recovery','account deletion','/app/','/console/'):
        assert truth.lower() in pages.lower()
