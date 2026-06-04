from app.tfs_auth import TfsAuth, TfsIdentity
from app.tfs_identity import identity_from_auth_login, identity_from_connection_user, merge_tfs_identities


def test_identity_from_connection_user_tele2_provider_display_name() -> None:
    identity = identity_from_connection_user(
        {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "descriptor": "aad.U-user123",
            "providerDisplayName": "TELE2\\user@t2.ru",
            "customDisplayName": "Иванов Иван",
            "uniqueName": "",
        }
    )
    assert identity.unique_name == "TELE2\\user@t2.ru"
    assert identity.email == "user@t2.ru"
    assert identity.descriptor == "aad.U-user123"
    assert identity.strong_tokens()
    assert "tele2\\user@t2.ru" in identity.match_tokens()


def test_identity_from_auth_login_expands_t2_email() -> None:
    identity = identity_from_auth_login(
        TfsAuth(
            base_url="https://tfs.t2.ru/tfs/Main",
            project="Tele2",
            username="ivanov@t2.ru",
        )
    )
    assert identity is not None
    assert "ivanov@t2.ru" in identity.match_tokens()
    assert "tele2\\ivanov" in identity.match_tokens()


def test_merge_tfs_identities_prefers_first_unique_name() -> None:
    merged = merge_tfs_identities(
        TfsIdentity(display_name="A", unique_name=None),
        TfsIdentity(unique_name="MAIN\\petrov", descriptor="aad.U-p"),
    )
    assert merged is not None
    assert merged.unique_name == "MAIN\\petrov"
    assert merged.display_name == "A"
