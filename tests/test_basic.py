
import pytest

from main import first_url, validate_url


def test_first_url():
    assert first_url("x https://example.com/a y") == "https://example.com/a"


def test_validate_public_url():
    validate_url("https://example.com")


def test_reject_localhost():
    with pytest.raises(Exception):
        validate_url("http://localhost/a")


def test_reject_private_ip():
    with pytest.raises(Exception):
        validate_url("http://127.0.0.1/a")
