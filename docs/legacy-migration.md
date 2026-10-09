# Legacy food migration

`curation/migrations/legacy-base-foods.json` is the migration contract from the current 946-row Train Libre base-food catalog to BLS 4.0.

Each old `products.barcode` is retained as `legacy_product_id`. Review each entry and set exactly one outcome:

- `mapped`: set `target_bls_code` to the BLS food that represents the same food. The generated SQLite stores its namespaced key (`bls:<code>`) as `target_barcode`.
- `no_equivalent`: leave the target null and record why the old food has no appropriate BLS equivalent.

`pending` is allowed only in preview output and blocks a strict app-asset build. This prevents recipe or share imports from silently losing old food references. The mapping applies when resolving imported legacy references; it does not rewrite users' existing diary entries.

The preparation command proposes candidates only when normalized German or English names match exactly. It records which source language matched. These suggestions are evidence for review, not decisions; ingredient variants, preparation states, and compound foods must be checked against the BLS names and nutrient identity.

The current draft contains 946 legacy records. Exact-name candidates are available for 123 records; 823 have no exact-name candidate. A missing candidate does not mean there is no equivalent—it means the names need manual comparison or an explicit `no_equivalent` decision.
