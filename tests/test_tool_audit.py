from meemee.tool_audit import redact


def test_recursive_secret_redaction_keeps_fingerprint():
    result = redact({"api_key":"abc", "nested":{"Authorization":"Bearer x"}, "safe":"ok"})
    assert result["api_key"]["redacted"]
    assert len(result["api_key"]["sha256"]) == 64
    assert result["nested"]["Authorization"]["redacted"]
    assert result["safe"] == "ok"
    assert "abc" not in str(result)


def test_large_text_is_bounded_and_fingerprinted():
    result = redact({"output":"x"*21000})["output"]
    assert result["truncated"] and result["bytes"] == 21000
    assert len(result["preview"]) == 1000
