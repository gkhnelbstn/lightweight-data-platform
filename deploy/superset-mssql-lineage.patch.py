"""Teach odd-collector's Superset adapter to resolve SQL Server datasets.

The adapter turns every Superset chart into a DataConsumer whose inputs are the
source tables behind it -- which is what makes "a check failed, so which
dashboards are wrong" answerable at all. It resolves those tables through a
per-backend adaptee, and ships with exactly two:

    SUPPORTED_BACKENDS = {"postgresql": ..., "sqlite": ...}

Anything else logs "Database backend mssql is not supported for generating
dataset oddrn" and the chart is ingested with no inputs. For an ERP on SQL
Server that is the whole feature, silently absent.

oddrn-generator already has MssqlGenerator, so this is the Postgres adaptee
with one class swapped. The ODDRN it mints has to be byte-identical to the one
odd-collector's own mssql adapter produces --

    //mssql/host/<host>/databases/<db>/schemas/<schema>/tables/<table>

-- or the chart points at a table that does not exist in the catalog.

Applied at image build time by deploy/Dockerfile.odd-collector.
"""
from pathlib import Path

TARGET = Path("/app/odd_collector/adapters/superset/domain/dataset.py")

ADAPTEE = '''

class MssqlGeneratorAdaptee:
    """Mirror of PostgresGeneratorAdaptee for SQL Server."""

    def get_dataset_oddrn(self, dataset: "Dataset"):
        try:
            database = dataset.database
            # `parameters` is only filled in when the connection was created
            # through Superset's dynamic form. A connection added as a plain
            # SQLAlchemy URI -- which is what the API and most operators
            # produce -- leaves it empty, so fall back to the URI itself.
            host = (database.parameters or {}).get("host")
            name = (database.parameters or {}).get("database")
            if not host or not name:
                url = make_url(database.sqlalchemy_uri)
                host = host or url.host
                name = name or url.database
            params = {
                "host_settings": host,
                "databases": name,
                "schemas": dataset.schema,
            }
            table_schema = dataset.schema
            table_name = dataset.name
            dataset_type = database.schemas[table_schema].tables[table_name].type
            if dataset_type not in ["view", "table"]:
                logger.warning(f"Dataset type {dataset_type} is not supported")
                return None
            dataset_type = "views" if dataset_type == "view" else "tables"
            params[dataset_type] = table_name
            return MssqlGenerator(**params).get_oddrn_by_path(dataset_type)
        except Exception as e:
            logger.warning(f"Failed to generate dataset oddrn: {e}")
            return None
'''


def main() -> None:
    src = TARGET.read_text(encoding="utf-8")
    if "MssqlGeneratorAdaptee" in src:
        print("already patched")
        return

    src = src.replace(
        "from oddrn_generator import PostgresqlGenerator, SQLiteGenerator",
        "from oddrn_generator import (MssqlGenerator, PostgresqlGenerator,\n"
        "                             SQLiteGenerator)")

    marker = "SUPPORTED_BACKENDS = {"
    if marker not in src:
        raise SystemExit("SUPPORTED_BACKENDS not found -- adapter has changed")
    src = src.replace(marker, ADAPTEE.strip() + "\n\n\n" + marker)
    src = src.replace(
        '    "sqlite": SqliteGeneratorAdaptee,\n}',
        '    "sqlite": SqliteGeneratorAdaptee,\n    "mssql": MssqlGeneratorAdaptee,\n}')

    TARGET.write_text(src, encoding="utf-8")
    assert '"mssql": MssqlGeneratorAdaptee' in TARGET.read_text(encoding="utf-8")
    print("patched: superset adapter now resolves mssql datasets")


if __name__ == "__main__":
    main()
