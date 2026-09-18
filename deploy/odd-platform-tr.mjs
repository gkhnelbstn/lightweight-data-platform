/**
 * Add Turkish to ODD Platform's own language picker.
 *
 * ODD already switches language: `SelectLanguage` lists `LANGUAGES_MAP` and
 * `i18n.ts` loads one catalogue per entry. It has no Turkish. The picker is
 * the right place for it rather than a switch of our own, because the whole
 * page changes language, ODD's screens as well as our panel. A second switch
 * would leave half the screen in the other language, which is what issue #44
 * set out to end.
 *
 * Three edits in two files, plus the catalogue itself
 * (deploy/odd-platform-locale-tr.json, copied in by the Dockerfile). The
 * `Lang` type is `keyof typeof LANGUAGES_MAP`, so it follows by itself.
 * `i18n.ts` also rejects a stored language that is not in its `resources`,
 * which is why the catalogue has to be registered there and not added later
 * from the panel.
 *
 * The catalogue is offered upstream. This is a carried patch (ADR 0011) and
 * leaves when upstream ships Turkish.
 *
 *   node deploy/odd-platform-tr.mjs <odd-platform checkout>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const root = process.argv[2];
if (!root) {
  console.error('usage: node odd-platform-tr.mjs <odd-platform checkout>');
  process.exit(2);
}

const ui = join(root, 'odd-platform-ui/src');
const edits = {
  'locales/i18n.ts': [
    {
      find: "import br from './translations/br.json';",
      add: "\nimport tr from './translations/tr.json';",
    },
    { find: '  br: { translation: br },', add: '\n  tr: { translation: tr },' },
  ],
  'lib/constants.ts': [
    { find: "  br: 'Brazilian Portuguese',", add: "\n  tr: 'Turkish'," },
    { find: "  br: 'br',\n} as const;", replace: "  br: 'br',\n  tr: 'tr',\n} as const;" },
  ],
};

for (const [name, anchors] of Object.entries(edits)) {
  const file = join(ui, name);
  let source = readFileSync(file, 'utf8');
  for (const anchor of anchors) {
    if (!source.includes(anchor.find)) {
      console.error(
        `FATAL: anchor not found in ${name}:\n${anchor.find}\n\n` +
          'Upstream moved it. If ODD now ships Turkish, delete this patch, the ' +
          'catalogue and the Dockerfile lines that use them.'
      );
      process.exit(1);
    }
    source = source.replace(anchor.find, anchor.replace ?? anchor.find + anchor.add);
  }
  writeFileSync(file, source);
}
console.log('i18n.ts, constants.ts: Turkish added to the language picker');
