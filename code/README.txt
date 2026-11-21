Machina CAD De-ID - attempt pipeline
====================================

What it does
------------
1. Harvest identities (deid/harvest.py, deid/identities.py)
   - paths: GitHub/GitLab account owners, web host names, user-home folders
   - native metadata: STEP FILE_NAME author/organization, STEP PERSON/ORGANIZATION,
     IGES global author/organization, OLE SummaryInformation (SolidWorks <2015,
     Inventor, Solid Edge), PDF Info + XMP, FreeCAD Document.xml, 3MF metadata
   - free text: e-mail addresses, C:\Users\<name>\ and /home/<name>/ paths
   - plus a hand-curated list of organisations/people/projects named in folder titles
2. Pseudonymize (deid/pseudo.py): keyed HMAC, token by token, same length, same case
   pattern, punctuation kept. Same original -> same pseudonym everywhere (content,
   metadata, file and folder names, archive member names); different originals differ.
   Identity fields get meaningful replacement names, never blanks.
3. Rewrite (deid/process.py): length-preserving byte replacement for every format so
   binary CAD files keep their offsets; format-aware field rewriting for STEP, IGES,
   OLE property sets, PDF (pikepdf: Info, XMP, strings, content streams, embedded files),
   FreeCAD/3MF XML; zip-based formats, gzip and tar are unpacked, rewritten member by
   member and repacked with the same compression. Geometry, units, materials and
   configurations are never touched.
4. Paths: identity tokens in folder/file names are replaced with the same pseudonyms used
   in file contents (so references stay consistent); extensions and non-identifying
   path parts are kept.

Run
---
  pip install olefile pikepdf pyahocorasick wordfreq   (versions: see requirements.txt)
  export MACHINA_KEY=<secret>        # pseudonym key, kept private
  python3 fetch.py manifest.tsv input 32
  python3 -m deid.harvest input > harvest.jsonl
  python3 -m deid.identities ids.json harvest.jsonl harvest_paths.jsonl
  python3 -m deid.runlocal ids.json input out/corpus report.jsonl
  (or python3 -m deid.driver ids.json manifest.tsv work gs://.../attempt-N/ to stream
   download -> rewrite -> upload in batches)

Limitations and exceptions
--------------------------
- SolidWorks 2015+ native files (proprietary compressed container), DWG R2004+ and
  Fusion 360 .f3d/.f3z archives that use zstd members are copied with only raw
  same-length replacement applied (internal compressed streams are not rewritten).
- Unix .Z (compress) and .7z archives are copied without member rewriting.
- Text inside raster images and logos is not altered.
- Source defects: the input contains duplicate files, empty files and some files that
  are invalid or truncated in the source (for example incomplete downloads with a
  .resume-partial suffix). They are kept as delivered, with only identity replacement
  applied; nothing was dropped.
