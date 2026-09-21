package org.opendatadiscovery.oddrn.model;

import lombok.Builder;
import lombok.Data;
import org.opendatadiscovery.oddrn.annotation.PathField;

/**
 * The ODDRN this platform mints for its own entities -- contract checks, their
 * runs, and declared pipeline jobs -- under ODD's `datafletch` extension.
 *
 * integrations/odd/mapper.py builds
 * {@code //datafletch/host/<h>/contracts/<id>/checks/<check>/runs/<date>} and
 * integrations/odd/lineage.py {@code //datafletch/host/<h>/transformers/<name>}.
 * Without a model the Directory files them under "Other"; with one they read
 * as "Data contracts".
 */
@Data
@Builder(toBuilder = true)
public class DatafletchPathsModel implements OddrnPath {
    @PathField
    private final String host;

    @PathField(dependency = "host", prefix = "contracts")
    private final String contract;

    @PathField(dependency = "contract", prefix = "checks")
    private final String check;

    @PathField(dependency = "check", prefix = "runs")
    private final String run;

    @PathField(dependency = "host", prefix = "transformers")
    private final String transformer;

    @Override
    public String prefix() {
        return "//datafletch";
    }

    @Override
    public String name() {
        return "data_contracts";
    }
}
