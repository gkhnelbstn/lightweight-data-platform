/**
 * An asset's Discussions tab, in Google Chat rather than Slack (ADR 0028).
 *
 * ODD's tab is built on its data-collaboration service, whose only provider
 * is Slack: with no Slack workspace there is no channel to post to, and this
 * team talks in Google Chat. One anchored line points the tab's lazy import at
 * our component (deploy/odd-platform-ui/Discussions.tsx); the route, the tab
 * and its visibility rules stay theirs. Fails the build when the line moves.
 *
 *   node deploy/odd-platform-discussions.mjs <odd-platform checkout>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2];
if (!root) {
  console.error('usage: node odd-platform-discussions.mjs <odd-platform checkout>');
  process.exit(2);
}

const file = join(
  root,
  'odd-platform-ui/src/components/DataEntityDetails/DataEntityDetailsRoutes/DataEntityDetailsRoutes.tsx'
);
const find = "const DataCollaboration = lazy(() => import('../DataCollaboration/DataCollaboration'));";
const replace =
  "const DataCollaboration = lazy(() => import('../../DataQuality/Contracts/Discussions'));";

const source = readFileSync(file, 'utf8');
if (source.split(find).length !== 2) {
  console.error(`odd-platform-discussions: anchor not found exactly once in ${file}`);
  process.exit(1);
}
writeFileSync(file, source.replace(find, replace));
console.log('odd-platform-discussions: Discussions tab points at the Google Chat panel');
