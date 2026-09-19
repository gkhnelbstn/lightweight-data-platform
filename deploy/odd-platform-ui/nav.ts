import i18n from 'locales/i18n';
import turkish from './tr.json';

/**
 * What the fork adds to ODD's own menu, kept apart from the panel so that the
 * menu -- loaded on every page -- pulls in a catalogue and a path, not the
 * panel (deploy/odd-platform-integration-tab.mjs).
 *
 * The panel's words live in ODD's i18n instance under a namespace of our own
 * (issue #44); the menu's tab label is one of them, so the catalogue is
 * registered here, before the menu first renders. Imported from `locales/i18n`
 * rather than `i18next` so the instance is initialised first.
 */
export const LDP = 'ldp';
i18n.addResourceBundle('tr', LDP, turkish, true, true);

export const integrationPath = () => '/integration';
