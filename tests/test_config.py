"""A URL do banco aguenta senha com caracteres que têm significado em URL."""

import pytest
from sqlalchemy.engine import make_url

from app.core.config import Settings


@pytest.mark.parametrize("password", ["simples123", "abc@123", "a/b:c#d", "abc%40x"])
def test_senha_do_banco_vai_escapada_na_url(password):
    url = make_url(Settings(db_password=password, db_host="db").database_url)
    assert (url.password, url.host) == (password, "db")
