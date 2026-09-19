/**
 * Give the two-way integration a tab of its own in ODD Platform's menu, #78.
 *
 * Asked for in so many words: the integration is not data quality, and a
 * sub-tab of our Data Quality panel would hide it where nobody looks for it.
 * The page is ours (deploy/odd-platform-ui/Integration.tsx); what ODD needs is
 * a menu entry and a route, four single-line anchors in two files, in the
 * same shape as deploy/odd-platform-dq-panel.mjs: an anchor that moves fails
 * the build rather than quietly dropping the tab. ADR 0022.
 *
 *   node deploy/odd-platform-integration-tab.mjs <odd-platform checkout>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2];
if (!root) {
  console.error('usage: node odd-platform-integration-tab.mjs <odd-platform checkout>');
  process.exit(2);
}

const ui = join(root, 'odd-platform-ui/src/components');
const edits = {
  'shared/elements/AppToolbar/ToolbarTabs/ToolbarTabs.tsx': [
    {
      find: "import AppTabs, { type AppTabItem } from 'components/shared/elements/AppTabs/AppTabs';",
      add: "\nimport { LDP, integrationPath } from 'components/DataQuality/Contracts/nav';",
    },
    {
      // Beside Data Quality, the other thing here that is ours.
      find: `        name: t('Data Quality'),
        link: dataQualityPath(),
        value: 'data-quality',
      },`,
      add: `
      {
        name: t('Integration', { ns: LDP }),
        link: integrationPath(),
        value: 'integration',
      },`,
    },
  ],
  'App.tsx': [
    {
      find: "const DataQuality = lazy(() => import('./DataQuality/DataQuality'));",
      add: "\nconst Integration = lazy(() => import('./DataQuality/Contracts/Integration'));",
    },
    {
      find: '            <Route path={dataQualityPath()} element={<DataQuality />} />',
      add: "\n            <Route path='/integration' element={<Integration />} />",
    },
  ],
};

for (const [name, anchors] of Object.entries(edits)) {
  const file = join(ui, name);
  let source = readFileSync(file, 'utf8');
  for (const anchor of anchors) {
    if (!source.includes(anchor.find)) {
      console.error(
        `FATAL: anchor not found in ${name}:\n${anchor.find}\n\n` +
          'Upstream moved it. Re-read the file and update this patch rather ' +
          'than pinning an older ODD_VERSION and forgetting why.'
      );
      process.exit(1);
    }
    source = source.replace(anchor.find, anchor.find + anchor.add);
  }
  writeFileSync(file, source);
}
console.log('ToolbarTabs.tsx, App.tsx: Integration tab added');
