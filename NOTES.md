# CAD De-ID pipeline (work in progress, paused 2026-11-21 ~14:25Z)

Status: harvest + identity build + rewriter run end-to-end on a 9 GB stratified sample
(5,679 files, ~4 min on 4 cores). Not yet validated (parse checks, leak scan), not yet
run on the full 139.7 GB corpus, nothing uploaded or submitted.

Flow:
1. `python3 code/fetch.py SELECTED.tsv input 32` (manifest rows from machina-input.joinbuilders.org/manifest.tsv)
2. `python3 -m deid.harvest input > harvest.jsonl` (+ path identities from the manifest via deid.harvest.path_identities)
3. `python3 -m deid.identities ids.json harvest.jsonl harvest_paths.jsonl`
4. `MACHINA_KEY=... python3 -m deid.runlocal ids.json input out/corpus report.jsonl`
5. upload.py --token-file for attempt-N/ (stream in batches: container disk is ~60 GB)

Next steps: validation script (STEP/PDF/zip/OLE parse + leak grep), silence pikepdf XMP warnings,
.Z/.7z archives, batch driver for the full corpus, README.txt.
