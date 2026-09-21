"""Make odd-collector's mssql adapter work on a Turkish-collation database.

Two defects, both measured against Siber2019 (`Turkish_CI_AS`):

1. The adapter writes INFORMATION_SCHEMA in lower case. The collation is
   case-insensitive, but in Turkish the upper case of `i` is `İ`, not `I`, so
   `information_schema` is not `INFORMATION_SCHEMA` and the first query dies:

       Invalid object name 'information_schema.table_constraints'.

   The views and their columns are upper case in every collation, so the
   upper-case spelling works everywhere. The `sys.*` catalog views are lower
   case and stay as written.

2. The primary- and foreign-key CTEs can return a column more than once (a
   column in two foreign keys, a constraint name reused across tables), the
   join repeats the column, and ODD refuses the whole push:

       IllegalStateException: Duplicate key .../columns/alistarifeid

   DISTINCT in both CTEs; the flags they feed are yes/no anyway.

Fails the build when the file no longer looks like the one this was written
against.
"""

import re
from pathlib import Path

PATH = Path("/app/odd_collector/adapters/mssql/repository.py")
# Every alias the three queries give an INFORMATION_SCHEMA view or a CTE over one.
ALIASES = "t|TC|KU|RC|CU|C|PK|FK|tvu|tb"
KEY_CTES = ("SELECT KU.table_catalog", "SELECT CU.table_catalog")

source = PATH.read_text()
for cte in KEY_CTES:
    if cte not in source:
        raise SystemExit(f"{PATH}: layout changed, patch not applied")
    source = source.replace(cte, cte.replace("SELECT", "SELECT DISTINCT"))
patched = re.sub(
    r"\binformation_schema\.([a-z_]+)",
    lambda m: "INFORMATION_SCHEMA." + m.group(1).upper(),
    source,
)
patched = re.sub(
    rf"\b({ALIASES})\.([a-z_]+)\b",
    lambda m: f"{m.group(1)}.{m.group(2).upper()}",
    patched,
)
if "information_schema." in patched or "INFORMATION_SCHEMA.TABLE_CONSTRAINTS" not in patched:
    raise SystemExit(f"{PATH}: layout changed, patch not applied")
PATH.write_text(patched)
