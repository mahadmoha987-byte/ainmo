# Address autocomplete

Ainmo's typeahead is deliberately separate from the full geocoder:

- /api/address-suggest searches a local Supabase PostgreSQL index and never
  calls Catastro or Nominatim.
- /api/geocode remains the authoritative exact/same-block/fallback resolver
  when a user submits an address manually.
- Selecting a suggestion sends its official coordinates and linked lot code
  directly to /api/calc; the existing lot-code mismatch guard still applies.

## Provisioning

1. Apply migrations/20260908_address_index.sql in the Supabase SQL editor.
2. Make SUPABASE_URL and SUPABASE_SERVICE_KEY available to the import process.
3. Validate a small official-data read without database writes:

       python3 tools/import_address_index.py --dry-run --limit 2000

4. Run the resumable full import:

       python3 tools/import_address_index.py --resume

5. Add the compact secondary/corner address aliases after the principal index
   is ready:

       python3 tools/import_address_index.py --secondary-aliases --resume

The first pass reads principal UAECD address plates (PDOTIPO=1), applies the
same normalization as the production geocoder, classifies treatment against
POT Layer 15 locally, and checkpoints `last_object_id` after every 2,000 source
records. The second pass stores PDOTIPO 2/3 plates in a compact alias table so
corner entrances and alternate plates resolve to the same cadastral lot. It
checkpoints independently and can run without taking principal search offline.
Re-run either command with `--resume` after any interruption.

The search RPC is disabled while the principal
`address_index_meta.status` is not ready, so users never receive a silently
partial principal city index. Results from both tables are deduplicated by lot,
preferring an exact match and then a principal plate.

## Refresh and QA

Re-run the importer when UAECD publishes a material address update. After a
refresh, run:

    python3 tools/address_suggest_audit.py \
      --base-url https://ainmo.uk \
      --samples artifacts/address_audit/samples.json

The audit probes 3, 6, 10, and full-string character lengths for every real
address, records top-eight recall, and reports p50/p95 endpoint latency.
