"""Start the PR's real Django project against a throwaway local SQLite file,
with every outbound network call blocked (nothing here may reach the feed,
Neon, or GitHub). Import this first, then call boot(repo, sqlite_path)."""

import os
import socket
import sys

_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect


def _local(host):
    return host in ("localhost", "127.0.0.1", "::1", None, "") or str(host).startswith("/")


def _guard_getaddrinfo(host, *args, **kwargs):
    if not _local(host):
        raise OSError(f"network blocked in replay: {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


def _guard_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if not _local(host):
        raise OSError(f"network blocked in replay: {address!r}")
    return _real_connect(self, address)


socket.getaddrinfo = _guard_getaddrinfo
socket.socket.connect = _guard_connect


def boot(repo, sqlite_path, fresh=True):
    if fresh and os.path.exists(sqlite_path):
        os.remove(sqlite_path)
    for key in [k for k in os.environ if k.startswith("DATABASE")]:
        del os.environ[key]
    os.environ["DATABASE_URL"] = f"sqlite:///{sqlite_path}"
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    os.environ["DJANGO_DEBUG"] = "1"
    os.environ["DOWNTOWNER_URL"] = "http://feed.invalid"
    os.environ.pop("SECRET_KEY", None)
    sys.path.insert(0, repo)
    import django

    django.setup()
    from django.conf import settings

    assert settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"), "not SQLite"
    from django.core.management import call_command

    call_command("migrate", verbosity=0)
