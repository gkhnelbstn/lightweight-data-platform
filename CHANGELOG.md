# Changelog

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
