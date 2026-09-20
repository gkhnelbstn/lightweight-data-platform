# Changelog

## [0.7.0](https://github.com/gkhnelbstn/lightweight-data-platform/compare/v0.6.2...v0.7.0) (2026-09-20)


### Features

* a contract's checks can be run from the screen ([#113](https://github.com/gkhnelbstn/lightweight-data-platform/issues/113)) ([1d3f37d](https://github.com/gkhnelbstn/lightweight-data-platform/commit/1d3f37d80435e566a75d4a9b572da319632607d6))
* a discussion about an asset needs a Slack workspace, and here is the wiring ([#110](https://github.com/gkhnelbstn/lightweight-data-platform/issues/110)) ([2f20a4f](https://github.com/gkhnelbstn/lightweight-data-platform/commit/2f20a4f0ee575f027ea426cee26ed3b09c14d3c3))
* a flow is configured on the Integration tab, and says how it runs ([#109](https://github.com/gkhnelbstn/lightweight-data-platform/issues/109)) ([10e4a52](https://github.com/gkhnelbstn/lightweight-data-platform/commit/10e4a528ce513973dff50e61189c7848fab3abde))
* a log line on the Integration tab opens its record and its history ([#104](https://github.com/gkhnelbstn/lightweight-data-platform/issues/104)) ([195861d](https://github.com/gkhnelbstn/lightweight-data-platform/commit/195861d092a9b023390b5e744e789187a3d6bae9))
* a Postgres system joins the hub, written with a guarded MERGE ([#103](https://github.com/gkhnelbstn/lightweight-data-platform/issues/103)) ([aff368b](https://github.com/gkhnelbstn/lightweight-data-platform/commit/aff368b6137de691f8b4bee497bcfb258d4d5d56))
* an API source is a poll into the hub, one way ([#124](https://github.com/gkhnelbstn/lightweight-data-platform/issues/124)) ([628b8f5](https://github.com/gkhnelbstn/lightweight-data-platform/commit/628b8f5ed4133cb75a43a1762c5b18adeaa4f3bc))
* many rows into one, one way: an aggregate summed where its rows land ([#105](https://github.com/gkhnelbstn/lightweight-data-platform/issues/105)) ([64727fd](https://github.com/gkhnelbstn/lightweight-data-platform/commit/64727fd2045d253e76472183b666378cf6748a47))
* server-written text follows one deployment language ([#101](https://github.com/gkhnelbstn/lightweight-data-platform/issues/101)) ([aa70ee5](https://github.com/gkhnelbstn/lightweight-data-platform/commit/aa70ee5c90998bfa23844c86b56817d886b65f5f))
* the arrivals chart shows the day, not only the busy hours ([#119](https://github.com/gkhnelbstn/lightweight-data-platform/issues/119)) ([d9785ef](https://github.com/gkhnelbstn/lightweight-data-platform/commit/d9785efc4b53c5194507e8b2e5bc81fe5a041780))
* the Checks tab says what the list adds up to ([#116](https://github.com/gkhnelbstn/lightweight-data-platform/issues/116)) ([ee19a4c](https://github.com/gkhnelbstn/lightweight-data-platform/commit/ee19a4c7a4e3f3f1891905cc349d1152b01048cc))
* the hub takes an API source, and stops losing records to a shared value ([3a5035c](https://github.com/gkhnelbstn/lightweight-data-platform/commit/3a5035c03f3eb24b0425a9b79e2defc8a1fdc4cc))
* the hub's golden records fill ODD's Master Data page ([#106](https://github.com/gkhnelbstn/lightweight-data-platform/issues/106)) ([a8bef36](https://github.com/gkhnelbstn/lightweight-data-platform/commit/a8bef3634ce76af5e78a78f0c02b27fe6144a0de))
* the Integration tab answers at a glance, filters, and settles a held row ([#112](https://github.com/gkhnelbstn/lightweight-data-platform/issues/112)) ([5789d8d](https://github.com/gkhnelbstn/lightweight-data-platform/commit/5789d8dbe5f0309badac48965a7034768c29a40f))


### Bug Fixes

* a checkpoint SeaTunnel cannot read is a refusal, not a traceback ([#127](https://github.com/gkhnelbstn/lightweight-data-platform/issues/127)) ([fcdee1f](https://github.com/gkhnelbstn/lightweight-data-platform/commit/fcdee1f6019151bb672c29c32dfeb797c5acd4c1))
* a record made beside one that shares its linkBy value is not delivered onto it ([#121](https://github.com/gkhnelbstn/lightweight-data-platform/issues/121)) ([c0abc90](https://github.com/gkhnelbstn/lightweight-data-platform/commit/c0abc9081f07e5203f13540ad77255c971f2bb62))
* a row the hub did nothing with is not history ([#135](https://github.com/gkhnelbstn/lightweight-data-platform/issues/135)) ([78c8126](https://github.com/gkhnelbstn/lightweight-data-platform/commit/78c8126ed9b464c1eaaf0e7aa0421e4f13dc66d8))
* ER diagrams open again, and the contracts say what they draw ([#108](https://github.com/gkhnelbstn/lightweight-data-platform/issues/108)) ([b2a9f6d](https://github.com/gkhnelbstn/lightweight-data-platform/commit/b2a9f6d37240f69487742f778072a9c1a6701c31))
* the customers contract described its key as an order's foreign key ([#114](https://github.com/gkhnelbstn/lightweight-data-platform/issues/114)) ([5e84b11](https://github.com/gkhnelbstn/lightweight-data-platform/commit/5e84b11693350a34096a9d50cf58993457f3c1ff))
* the Http source lets a checkpoint through, and the API demo runs ([#133](https://github.com/gkhnelbstn/lightweight-data-platform/issues/133)) ([af33df3](https://github.com/gkhnelbstn/lightweight-data-platform/commit/af33df3a64ef8dc5367d1296201e8aa8d1f5e8f2))
* the hub's own insert finds the record that caused it ([#123](https://github.com/gkhnelbstn/lightweight-data-platform/issues/123)) ([432a565](https://github.com/gkhnelbstn/lightweight-data-platform/commit/432a5659f946b19a87f570c1ec077a5bdfb0a9e0))
* the linkBy flag follows the value, and a delivery answered late is still ours ([#131](https://github.com/gkhnelbstn/lightweight-data-platform/issues/131)) ([1dbc586](https://github.com/gkhnelbstn/lightweight-data-platform/commit/1dbc586ee6cb63006d2fc101b607487ea4675e90))
* what match pins must be part of the table's key ([#130](https://github.com/gkhnelbstn/lightweight-data-platform/issues/130)) ([56bbc3b](https://github.com/gkhnelbstn/lightweight-data-platform/commit/56bbc3bb8fc7fd6731e7643c00aca0e4f6aab9d6))


### Documentation

* a key change is a delete and an insert on both engines ([#132](https://github.com/gkhnelbstn/lightweight-data-platform/issues/132)) ([3a45d5a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/3a45d5a2f37ed194ee2f2bc7357253fbb2584517))
* say that the app image does not reload mounted code ([#117](https://github.com/gkhnelbstn/lightweight-data-platform/issues/117)) ([c8bfa79](https://github.com/gkhnelbstn/lightweight-data-platform/commit/c8bfa7912c4518f2dff52bedb66d90d0b1834626))
* the ER-diagram patch tracks the upstream issue that already existed ([#118](https://github.com/gkhnelbstn/lightweight-data-platform/issues/118)) ([60d3ac3](https://github.com/gkhnelbstn/lightweight-data-platform/commit/60d3ac3a8b922408ae3346e7fbb9255a9b715ac2))
* the upstream bug report for the Http source's checkpoint lock ([#134](https://github.com/gkhnelbstn/lightweight-data-platform/issues/134)) ([2a9fd57](https://github.com/gkhnelbstn/lightweight-data-platform/commit/2a9fd57ecb7d69d7bc539db156bfe22f0a4caf78))

## [0.6.2](https://github.com/gkhnelbstn/lightweight-data-platform/compare/v0.6.1...v0.6.2) (2026-09-19)


### Bug Fixes

* recent times on the panel read in minutes, not as an hour ([#99](https://github.com/gkhnelbstn/lightweight-data-platform/issues/99)) ([380931b](https://github.com/gkhnelbstn/lightweight-data-platform/commit/380931b4b8844182de6cf851c0c8830bc5346706))

## [0.6.1](https://github.com/gkhnelbstn/lightweight-data-platform/compare/v0.6.0...v0.6.1) (2026-09-19)


### Bug Fixes

* an awaited value the system already had can no longer swallow an edit ([#96](https://github.com/gkhnelbstn/lightweight-data-platform/issues/96)) ([dc37c2c](https://github.com/gkhnelbstn/lightweight-data-platform/commit/dc37c2c444f29e0bfcab7e3db7ac66661d4c9e76))

## [0.6.0](https://github.com/gkhnelbstn/lightweight-data-platform/compare/v0.5.0...v0.6.0) (2026-09-19)


### Features

* a table that changed under its flow is refused and shown, not followed ([#94](https://github.com/gkhnelbstn/lightweight-data-platform/issues/94)) ([9b23ec0](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9b23ec03036757e1e8a611b32d9f0e63a11cc894))

## [0.5.0](https://github.com/gkhnelbstn/lightweight-data-platform/compare/v0.4.1...v0.5.0) (2026-09-19)


### Features

* a value outside a value map is logged and kept out, not written as empty ([#92](https://github.com/gkhnelbstn/lightweight-data-platform/issues/92)) ([c7520dd](https://github.com/gkhnelbstn/lightweight-data-platform/commit/c7520dd5cda653110d508b006e5bfbc9bc2233f2))

## [0.4.1](https://github.com/gkhnelbstn/lightweight-data-platform/compare/v0.4.0...v0.4.1) (2026-09-19)


### Bug Fixes

* a flow resumes where it stopped after a SeaTunnel restart, or refuses to ([#90](https://github.com/gkhnelbstn/lightweight-data-platform/issues/90)) ([9ab973e](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9ab973e14bcd2aa68559c1f763f124401a92499f))

## [0.4.0](https://github.com/gkhnelbstn/lightweight-data-platform/compare/v0.3.0...v0.4.0) (2026-09-19)


### Features

* a replication target can be a view over the source ([#38](https://github.com/gkhnelbstn/lightweight-data-platform/issues/38)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* checks get their own screen, and the contract panel gets tabs ([#21](https://github.com/gkhnelbstn/lightweight-data-platform/issues/21)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* integration flows are their own files, with value maps, compiled into SeaTunnel jobs ([#68](https://github.com/gkhnelbstn/lightweight-data-platform/issues/68), [#69](https://github.com/gkhnelbstn/lightweight-data-platform/issues/69), [#74](https://github.com/gkhnelbstn/lightweight-data-platform/issues/74)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* rule kinds can come from other packages ([#37](https://github.com/gkhnelbstn/lightweight-data-platform/issues/37)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* several tables of one system feed one record ([#85](https://github.com/gkhnelbstn/lightweight-data-platform/issues/85)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* systems with different codes share one record through a crosswalk ([#86](https://github.com/gkhnelbstn/lightweight-data-platform/issues/86)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* the integration has its own tab in ODD's menu ([#87](https://github.com/gkhnelbstn/lightweight-data-platform/issues/87)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* the platform can say that something broke ([#43](https://github.com/gkhnelbstn/lightweight-data-platform/issues/43)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* the target fills the columns only it can fill ([#48](https://github.com/gkhnelbstn/lightweight-data-platform/issues/48)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* the target says which of its columns come from where ([#55](https://github.com/gkhnelbstn/lightweight-data-platform/issues/55)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* Turkish in ODD's own language picker, and the panel follows it ([#65](https://github.com/gkhnelbstn/lightweight-data-platform/issues/65)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* two-way integration through a hub, where the latest commit wins and echoes are awaited ([#75](https://github.com/gkhnelbstn/lightweight-data-platform/issues/75), [#77](https://github.com/gkhnelbstn/lightweight-data-platform/issues/77)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))


### Bug Fixes

* a contract that states decimal(14,2) gets numeric(14,2) in the replica ([#60](https://github.com/gkhnelbstn/lightweight-data-platform/issues/60)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* a re-apply no longer stops replication ([#18](https://github.com/gkhnelbstn/lightweight-data-platform/issues/18), [#36](https://github.com/gkhnelbstn/lightweight-data-platform/issues/36)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* a reseed no longer breaks CDC, and a stale watermark self-heals ([#20](https://github.com/gkhnelbstn/lightweight-data-platform/issues/20)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* a shared link keeps matching the screen after ODD rewrites the URL ([#42](https://github.com/gkhnelbstn/lightweight-data-platform/issues/42)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* every lineage node draws its own icon ([#39](https://github.com/gkhnelbstn/lightweight-data-platform/issues/39)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))
* every warehouse contract is windowed ([#47](https://github.com/gkhnelbstn/lightweight-data-platform/issues/47)) ([9879e0a](https://github.com/gkhnelbstn/lightweight-data-platform/commit/9879e0a73c2c019f58b2bb98979532e0c9013eba))


### Documentation

* 224 tests after the merge ([c9a2686](https://github.com/gkhnelbstn/lightweight-data-platform/commit/c9a2686a7e1111bcf44da298e85711a64a6ea5bd))
* the suite is 210 tests, not 185 ([90d5cf0](https://github.com/gkhnelbstn/lightweight-data-platform/commit/90d5cf02628fc00b3d3c2cd6e4d0a3c887223cf8))
