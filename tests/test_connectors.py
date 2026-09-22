from meemee.connectors import ICSConnector, RSSConnector, SignedWebhookConnector


def test_rss_connector_normalizes_real_feed_shape(monkeypatch):
 xml=b'<rss><channel><item><guid>g1</guid><title>News</title><description>Body</description><pubDate>Tue, 22 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>'
 connector=RSSConnector('rss','https://example.com/feed');monkeypatch.setattr(connector,'read',lambda:xml)
 row=connector.fetch('u','feed')[0];assert row.external_id=='g1' and row.provenance['url'].endswith('/feed')

def test_ics_connector_normalizes_event(monkeypatch):
 body=b'BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:e1\r\nDTSTART:20260922T120000Z\r\nSUMMARY:Call\r\nDESCRIPTION:Discuss launch\r\nEND:VEVENT\r\nEND:VCALENDAR'
 connector=ICSConnector('ics','https://example.com/calendar.ics');monkeypatch.setattr(connector,'read',lambda:body)
 row=connector.fetch('u','calendar')[0];assert row.external_id=='e1' and row.kind=='event' and 'launch' in row.content

def test_signed_webhook_requires_canonical_fields():
 row=SignedWebhookConnector.parse('u','hook',{'id':'1','kind':'event','title':'T','content':'C','occurred_at':'2026-09-22T00:00:00Z'})
 assert row.provenance['received'] is True
