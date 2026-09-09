-- Fast local address autocomplete for Bogotá.
-- Safe to run more than once in the Supabase SQL editor.

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
SET search_path = public, extensions;

CREATE TABLE IF NOT EXISTS public.address_index (
  source_address_id   text PRIMARY KEY,
  source_object_id    bigint,
  address             text NOT NULL,
  normalized_address  text NOT NULL CHECK (normalized_address <> ''),
  lot_code             text NOT NULL CHECK (lot_code ~ '^[0-9]{12}$'),
  lat                  double precision NOT NULL CHECK (lat BETWEEN 3.6 AND 4.9),
  lng                  double precision NOT NULL CHECK (lng BETWEEN -75.1 AND -73.9),
  treatment            text,
  locality             text,
  neighborhood         text,
  source_snapshot_date date NOT NULL DEFAULT CURRENT_DATE,
  indexed_at           timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.address_index ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS address_index_lot_code_idx
  ON public.address_index (lot_code);
CREATE INDEX IF NOT EXISTS address_index_normalized_prefix_idx
  ON public.address_index (normalized_address text_pattern_ops);
CREATE INDEX IF NOT EXISTS address_index_normalized_trgm_idx
  ON public.address_index USING gin (normalized_address gin_trgm_ops);

CREATE TABLE IF NOT EXISTS public.address_index_meta (
  singleton             boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  status                text NOT NULL DEFAULT 'empty'
                        CHECK (status IN ('empty', 'importing', 'ready', 'failed')),
  imported_count        bigint NOT NULL DEFAULT 0,
  source_count          bigint,
  source_snapshot_date  date,
  last_object_id        bigint NOT NULL DEFAULT 0,
  started_at            timestamptz,
  completed_at          timestamptz,
  error                 text
);

ALTER TABLE public.address_index_meta ENABLE ROW LEVEL SECURITY;

INSERT INTO public.address_index_meta (singleton)
VALUES (true)
ON CONFLICT (singleton) DO NOTHING;

CREATE OR REPLACE FUNCTION public.search_address_index(
  p_query text,
  p_lat double precision DEFAULT NULL,
  p_lng double precision DEFAULT NULL,
  p_recent_lot_codes text[] DEFAULT ARRAY[]::text[],
  p_limit integer DEFAULT 8
)
RETURNS TABLE (
  address text,
  normalized_address text,
  lot_code text,
  treatment text,
  lat double precision,
  lng double precision,
  locality text,
  neighborhood text
)
LANGUAGE sql
STABLE
SET search_path = public, extensions
AS $$
  WITH exact_candidates AS MATERIALIZED (
    SELECT ai.*, 3 AS match_class
    FROM public.address_index AS ai
    WHERE ai.normalized_address = p_query
      AND EXISTS (
        SELECT 1 FROM public.address_index_meta AS meta
        WHERE meta.singleton AND meta.status = 'ready'
      )
    LIMIT 64
  ),
  prefix_candidates AS MATERIALIZED (
    SELECT ai.*, 2 AS match_class
    FROM public.address_index AS ai
    WHERE length(p_query) >= 3
      AND EXISTS (
        SELECT 1 FROM public.address_index_meta AS meta
        WHERE meta.singleton AND meta.status = 'ready'
      )
      AND ai.normalized_address LIKE p_query || '%'
      AND ai.normalized_address <> p_query
    -- Keep the indexed prefix scan bounded before optional proximity sorting.
    LIMIT 512
  ),
  direct_candidates AS MATERIALIZED (
    SELECT * FROM exact_candidates
    UNION ALL
    SELECT * FROM prefix_candidates
  ),
  road_prefix AS MATERIALIZED (
    SELECT CASE
      WHEN split_part(p_query, ' ', 3) ~ '^(S|E|BIS[A-Z]*)$'
      THEN concat_ws(
        ' ',
        split_part(p_query, ' ', 1),
        split_part(p_query, ' ', 2),
        split_part(p_query, ' ', 3)
      )
      ELSE concat_ws(
        ' ',
        split_part(p_query, ' ', 1),
        split_part(p_query, ' ', 2)
      )
    END AS value
    WHERE length(p_query) >= 8
      AND p_query LIKE '% % % %'
      AND split_part(p_query, ' ', 1) IN ('AC', 'AK', 'AV', 'CL', 'DG', 'KR', 'TV')
      AND split_part(p_query, ' ', 2) <> ''
  ),
  road_candidates AS MATERIALIZED (
    SELECT ai.*
    FROM public.address_index AS ai
    CROSS JOIN road_prefix AS road
    WHERE EXISTS (
        SELECT 1 FROM public.address_index_meta AS meta
        WHERE meta.singleton AND meta.status = 'ready'
      )
      -- Typo recovery is bounded to the same numbered road. It never scans
      -- the entire city when a user mistypes a cross street or door number.
      AND NOT EXISTS (SELECT 1 FROM direct_candidates)
      AND ai.normalized_address LIKE road.value || '%'
    LIMIT 1024
  ),
  fuzzy_candidates AS MATERIALIZED (
    SELECT ai.*, 1 AS match_class
    FROM road_candidates AS ai
    WHERE similarity(ai.normalized_address, p_query) >= 0.30
    ORDER BY similarity(ai.normalized_address, p_query) DESC,
             ai.normalized_address,
             ai.source_address_id
    LIMIT 128
  ),
  candidates AS (
    SELECT DISTINCT ON (source_address_id) *
    FROM (
      SELECT * FROM direct_candidates
      UNION ALL
      SELECT * FROM fuzzy_candidates
    ) AS combined
    ORDER BY source_address_id, match_class DESC
  )
  SELECT
    c.address,
    c.normalized_address,
    c.lot_code,
    c.treatment,
    c.lat,
    c.lng,
    c.locality,
    c.neighborhood
  FROM candidates AS c
  ORDER BY
    c.match_class DESC,
    CASE
      WHEN p_lat IS NOT NULL AND p_lng IS NOT NULL
      THEN power(c.lat - p_lat, 2) + power(c.lng - p_lng, 2)
      ELSE NULL
    END ASC NULLS LAST,
    similarity(c.normalized_address, p_query) DESC,
    CASE
      WHEN c.lot_code = ANY(COALESCE(p_recent_lot_codes, ARRAY[]::text[]))
      THEN array_position(p_recent_lot_codes, c.lot_code)
      ELSE 2147483647
    END ASC,
    c.address,
    c.lot_code
  LIMIT LEAST(GREATEST(COALESCE(p_limit, 8), 1), 8);
$$;

REVOKE ALL ON TABLE public.address_index FROM anon, authenticated;
REVOKE ALL ON TABLE public.address_index_meta FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.address_index TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.address_index_meta TO service_role;
REVOKE ALL ON FUNCTION public.search_address_index(
  text, double precision, double precision, text[], integer
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.search_address_index(
  text, double precision, double precision, text[], integer
) TO service_role;

COMMENT ON TABLE public.address_index IS
  'Principal UAECD address plates, normalized for Ainmo typeahead; final regulation remains live GIS.';
COMMENT ON FUNCTION public.search_address_index(
  text, double precision, double precision, text[], integer
) IS
  'Ranked local-only Bogotá address suggestions. Never calls external GIS services.';
