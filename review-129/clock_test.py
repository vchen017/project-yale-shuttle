"""Run test labels as if today were another date: django.utils.timezone.now
returns that moment (the clock the job and the tests read). Network blocked.

Usage: python clock_test.py <repo dir> <UTC ISO moment> <test labels...>
"""
import datetime as dt
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import boot as _guard  # noqa: E402,F401  (installs the network guard)

repo, moment = sys.argv[1:3]
labels = sys.argv[3:]
when = dt.datetime.fromisoformat(moment)
os.chdir(repo)
sys.path.insert(0, repo)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["DJANGO_DEBUG"] = "1"
for key in [k for k in os.environ if k.startswith("DATABASE")]:
    del os.environ[key]

from django.utils import timezone  # noqa: E402

patcher = mock.patch.object(timezone, "now", return_value=when)
patcher.start()
print(f"timezone.now() -> {timezone.now().isoformat()}", flush=True)
from django.core.management import execute_from_command_line  # noqa: E402

execute_from_command_line(["manage.py", "test", "-v", "2", *labels])
