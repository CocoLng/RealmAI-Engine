---
name: No DB migration functions
description: Delete and recreate the DB instead of writing migration code — avoids dead code
type: feedback
---

Do not add new migration functions to `db/database.py`. The DB is simply deleted and recreated from the SQLAlchemy models.

**Why:** Adding migrations creates dead code since the project just drops and recreates `data/realmai.db`. The existing migrations are legacy.

**How to apply:** When a new column is added to a model in `db/models.py`, the fix is to delete `data/realmai.db` — no code change needed for the schema.
