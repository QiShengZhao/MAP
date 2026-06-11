from app.api.deps import create_token, decode_token, hash_password, verify_password


def test_password_hash_roundtrip():
    h = hash_password("Str0ng!Passw0rd")
    assert verify_password("Str0ng!Passw0rd", h)
    assert not verify_password("wrong", h)


def test_jwt_roundtrip():
    token = create_token("user-123")
    assert decode_token(token) == "user-123"
