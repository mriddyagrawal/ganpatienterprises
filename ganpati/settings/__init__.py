"""
Settings package for the Ganpati Enterprises web app.

Loads `dev.py` by default. When a prod environment is set up later
(see futureplans.md #3), add `prod.py` and switch on the
DJANGO_ENV environment variable.
"""

import os

_env = os.environ.get("DJANGO_ENV", "dev").lower()

if _env == "prod":
    from .prod import *  # noqa: F401,F403  (added in a future phase)
else:
    from .dev import *  # noqa: F401,F403
