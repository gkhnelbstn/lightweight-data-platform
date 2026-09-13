/**
 * Render the Contracts panel inside ODD Platform's own Data Quality page.
 *
 * Three single-line anchors against upstream, and they are a patch rather than
 * a vendored copy of the file so that an upstream change to the page is a
 * build failure here rather than a silent revert of everything they did to it.
 * The panel itself is ours and lives in `deploy/odd-platform-ui/`.
 *
 * Two of the anchors only tag their own two sections with a class name. The
 * panel hides them while it is showing one of its own tabs and puts them back
 * on its last one -- see `Contracts.tsx`. Nothing of theirs is removed or
 * reordered: a class attribute is the whole change, so a future upstream edit
 * to those sections still applies to itself.
 *
 *   node deploy/odd-platform-dq-panel.mjs <odd-platform checkout>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2];
if (!root) {
  console.error('usage: node odd-platform-dq-panel.mjs <odd-platform checkout>');
  process.exit(2);
}

const file = join(
  root,
  'odd-platform-ui/src/components/DataQuality/DataQualityContent.tsx'
);
let source = readFileSync(file, 'utf8');

/** Their sections carry this so the panel can show and hide them. Kept in
 * sync by hand with the same constant in deploy/odd-platform-ui/shared.tsx. */
const CLASS = 'odd-dq-dashboard';

const anchors = [
  {
    find: "import TestCategoryResults from './TestResults/TestCategoryResults';",
    add: "\nimport { Contracts } from './Contracts/Contracts';",
  },
  {
    // The first section: two donuts and the category table. Our panel goes
    // in above it, because the question this page is opened with is "what
    // failed", and that is a list rather than a pie.
    find: `      <S.Section>
        <S.DashboardLegend>
          {Object.values(DataEntityRunStatus).map(status => (`,
    replace: `      <Contracts />
      <S.Section className='${CLASS}'>
        <S.DashboardLegend>
          {Object.values(DataEntityRunStatus).map(status => (`,
  },
  {
    // The second section: monitored vs unmonitored tables.
    find: `      <S.Section>
        <S.DashboardLegend>
          <S.DashboardLegendItem $status={DataEntityRunStatus.SUCCESS}>`,
    replace: `      <S.Section className='${CLASS}'>
        <S.DashboardLegend>
          <S.DashboardLegendItem $status={DataEntityRunStatus.SUCCESS}>`,
  },
];

for (const anchor of anchors) {
  if (!source.includes(anchor.find)) {
    console.error(
      `FATAL: anchor not found in DataQualityContent.tsx:\n${anchor.find}\n\n` +
        'Upstream moved it. Re-read the file and update this patch rather ' +
        'than pinning an older ODD_VERSION and forgetting why.'
    );
    process.exit(1);
  }
  source = anchor.replace
    ? source.replace(anchor.find, anchor.replace)
    : source.replace(anchor.find, anchor.find + anchor.add);
}

writeFileSync(file, source);
console.log('DataQualityContent.tsx: Contracts panel added');
