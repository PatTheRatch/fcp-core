"""Database package.

Importing this package registers every ORM model on ``Base.metadata``, so
Alembic autogeneration and the migration/metadata drift test both see the
full schema no matter which module the caller imported first.
"""

from app.db import models as models
