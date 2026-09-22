import pytest

from meemee.device_protocol import ReplayGuard, make_command, verify_command


def test_signed_command_validation_capability_and_replay():
    secret = b"x" * 32
    command = make_command(
        "phone", "camera.capture", {"quality": 80}, secret, issued_at=100, nonce="n", command_id="c"
    )
    guard = ReplayGuard(clock=lambda: 100)
    verified = verify_command(
        command,
        secret,
        expected_device_id="phone",
        allowed_capabilities={"camera.capture"},
        replay_guard=guard,
        now=100,
    )
    assert verified.arguments == {"quality": 80}
    with pytest.raises(ValueError, match="replayed"):
        verify_command(command, secret, replay_guard=guard, now=100)


def test_tamper_stale_wrong_device_and_undeclared_capability():
    secret = b"y" * 32
    command = make_command("laptop", "notify", {"text": "hi"}, secret, issued_at=100)
    tampered = {**command, "arguments": {"text": "bye"}}
    with pytest.raises(ValueError, match="signature"):
        verify_command(tampered, secret, now=100)
    with pytest.raises(ValueError, match="stale"):
        verify_command(command, secret, now=1000)
    with pytest.raises(ValueError, match="another device"):
        verify_command(command, secret, expected_device_id="phone", now=100)
    with pytest.raises(PermissionError):
        verify_command(command, secret, allowed_capabilities={"camera"}, now=100)
