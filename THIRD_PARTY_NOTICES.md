# Third-party data notices

## Bundeslebensmittelschlüssel (BLS) 4.0

Copyright and data source: Max Rubner-Institut (MRI).

> Max Rubner-Institut (2025): Bundeslebensmittelschlüssel (BLS), Version 4.0 — Deutsche Nährstoffdatenbank. Karlsruhe. DOI: [10.25826/Data20251217-134202-0](https://doi.org/10.25826/Data20251217-134202-0).

License: [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

Train Libre's derived catalog converts the source spreadsheets to SQLite, preserves source nutrient values and per-value provenance, and adds translated names, curated search aliases, Train Libre category assignments, and optional default portions. These are Train Libre modifications; in particular, `food_aliases` contains Train Libre curated additions, not BLS source content. The original source files are retained under `sources/bls/4.0/`.

This notice must be included with any distributed copy of the derived catalog, including the catalog bundled with Train Libre. The app's legal/third-party notices should identify the source, version, DOI, license, and the modifications made.
