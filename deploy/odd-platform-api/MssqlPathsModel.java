package org.opendatadiscovery.oddrn.model;

import lombok.Builder;
import lombok.Data;
import org.opendatadiscovery.oddrn.annotation.PathField;

/**
 * The SQL Server ODDRN, which oddrn-generator-java 0.1.21 does not know.
 *
 * odd-collector mints {@code //mssql/host/<h>/databases/<d>/schemas/<s>/tables/<t>},
 * and ODD's Directory parses a data source's ODDRN with the Java generator to
 * decide its type. With no model for the prefix every SQL Server source lands
 * in "Other". The generator finds its models by scanning this package, so one
 * class here is the whole fix: the same shape as PostgreSqlPath, which is also
 * the shape the Python generator gives MSSQL.
 */
@Data
@Builder(toBuilder = true)
public class MssqlPathsModel implements OddrnPath {
    @PathField
    private final String host;

    @PathField(dependency = "host", prefix = "databases")
    private final String database;

    @PathField(dependency = "database", prefix = "schemas")
    private final String schema;

    @PathField(dependency = "schema", prefix = "tables")
    private final String table;

    @PathField(dependency = "schema", prefix = "views")
    private final String view;

    @PathField(dependency = {"table", "view"}, prefix = "columns")
    private final String column;

    @Override
    public String prefix() {
        return "//mssql";
    }

    @Override
    public String name() {
        return "mssql";
    }
}
