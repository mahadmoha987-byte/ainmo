-- Edificabilidad Bogotá — Supabase Schema
-- Run in: Dashboard → SQL editor → New query
-- After running, configure Google OAuth in: Auth → Providers → Google

-- ── 1. profiles ─────────────────────────────────────────────────────────────
-- Extends auth.users; auto-populated by trigger on every signup.
CREATE TABLE public.profiles (
  id         uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email      text NOT NULL,
  name       text,
  avatar_url text,
  created_at timestamptz NOT NULL DEFAULT now(),
  plan       text NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro'))
);
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

-- Auto-create profile on first sign-up (Google OAuth or magic link).
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  INSERT INTO public.profiles (id, email, name, avatar_url)
  VALUES (
    NEW.id,
    NEW.email,
    COALESCE(
      NEW.raw_user_meta_data->>'full_name',
      NEW.raw_user_meta_data->>'name'
    ),
    NEW.raw_user_meta_data->>'avatar_url'
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();


-- ── 2. analyses ──────────────────────────────────────────────────────────────
-- Full result snapshot kept for reproducibility — GIS layers and the decree
-- text can change; the stored JSON always reflects what was true at query time.
CREATE TABLE public.analyses (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  direccion      text NOT NULL DEFAULT '',
  lote_codigo    text,
  lat            double precision NOT NULL,
  lng            double precision NOT NULL,
  result_json    jsonb NOT NULL,          -- {lookup_snapshot, calc_result, vis_en_sitio}
  decree_version text NOT NULL DEFAULT 'D.555/2021 · D.466/2024 · compilación D.670/2025',
  fetched_at     timestamptz NOT NULL DEFAULT now(),
  created_at     timestamptz NOT NULL DEFAULT now(),
  notas          text NOT NULL DEFAULT '',
  tags           text[] NOT NULL DEFAULT '{}'
);
ALTER TABLE public.analyses ENABLE ROW LEVEL SECURITY;
CREATE INDEX ON public.analyses (user_id, created_at DESC);


-- ── 3. usage ─────────────────────────────────────────────────────────────────
-- Monthly per-user lookup counter. Only the server (service_role) may write.
-- Client-side reads are allowed via RLS so the UI can show usage.
CREATE TABLE public.usage (
  user_id      uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
  month        text NOT NULL,   -- YYYY-MM
  lookup_count integer NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, month)
);
ALTER TABLE public.usage ENABLE ROW LEVEL SECURITY;

-- Atomic increment via RPC (avoids read-then-write race condition).
CREATE OR REPLACE FUNCTION public.increment_usage(p_user_id uuid, p_month text)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_count integer;
BEGIN
  INSERT INTO public.usage (user_id, month, lookup_count)
  VALUES (p_user_id, p_month, 1)
  ON CONFLICT (user_id, month) DO UPDATE
    SET lookup_count = usage.lookup_count + 1
  RETURNING lookup_count INTO v_count;
  RETURN v_count;
END;
$$;


-- ── 4. billing_interest ──────────────────────────────────────────────────────
-- Waitlist capture; billing not yet implemented. Clean interface for future
-- payment integration: add stripe_session_id / mercadopago_ref columns here.
CREATE TABLE public.billing_interest (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email      text NOT NULL,
  notes      text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.billing_interest ENABLE ROW LEVEL SECURITY;


-- ── 5. share_tokens ────────────────────────────────────────────────────────
-- Opaque, revocable links to immutable analysis snapshots. Public visitors
-- receive only the snapshot selected by the server, never portfolio access.
CREATE TABLE IF NOT EXISTS public.share_tokens (
  token             text PRIMARY KEY,
  analysis_id       uuid NOT NULL REFERENCES public.analyses(id) ON DELETE CASCADE,
  include_proforma  boolean NOT NULL DEFAULT false,
  created_at        timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.share_tokens ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS share_tokens_analysis_id_idx ON public.share_tokens (analysis_id);


-- ── RLS Policies ─────────────────────────────────────────────────────────────
-- Explicit deny-by-default: only named operations are permitted.

-- profiles: read/write own row only
CREATE POLICY "profiles_select_own"
  ON public.profiles FOR SELECT TO authenticated
  USING (id = auth.uid());

CREATE POLICY "profiles_insert_own"
  ON public.profiles FOR INSERT TO authenticated
  WITH CHECK (id = auth.uid());

CREATE POLICY "profiles_update_own"
  ON public.profiles FOR UPDATE TO authenticated
  USING (id = auth.uid())
  WITH CHECK (id = auth.uid());

-- analyses: full CRUD on own rows only
CREATE POLICY "analyses_select_own"
  ON public.analyses FOR SELECT TO authenticated
  USING (user_id = auth.uid());

CREATE POLICY "analyses_insert_own"
  ON public.analyses FOR INSERT TO authenticated
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "analyses_update_own"
  ON public.analyses FOR UPDATE TO authenticated
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "analyses_delete_own"
  ON public.analyses FOR DELETE TO authenticated
  USING (user_id = auth.uid());

-- usage: clients may read their own counter; writes are service_role only
CREATE POLICY "usage_select_own"
  ON public.usage FOR SELECT TO authenticated
  USING (user_id = auth.uid());
-- No INSERT/UPDATE/DELETE policy: service_role key bypasses RLS for writes.

-- billing_interest: anyone may insert (anon waitlist capture); no read
CREATE POLICY "billing_interest_insert_any"
  ON public.billing_interest FOR INSERT TO anon, authenticated
  WITH CHECK (true);


-- ── 6. address_index ─────────────────────────────────────────────────────────
-- Persistent, server-only autocomplete corpus. Import principal UAECD plates
-- with tools/import_address_index.py after applying this schema.
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
  address_type         smallint NOT NULL DEFAULT 1 CHECK (address_type BETWEEN 1 AND 3),
  treatment            text,
  locality             text,
  neighborhood         text,
  source_snapshot_date date NOT NULL DEFAULT CURRENT_DATE,
  indexed_at           timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.address_index ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.address_index
  ADD COLUMN IF NOT EXISTS address_type smallint NOT NULL DEFAULT 1
  CHECK (address_type BETWEEN 1 AND 3);

CREATE INDEX IF NOT EXISTS address_index_lot_code_idx
  ON public.address_index (lot_code);
CREATE INDEX IF NOT EXISTS address_index_normalized_prefix_idx
  ON public.address_index (normalized_address text_pattern_ops);
CREATE INDEX IF NOT EXISTS address_index_normalized_trgm_idx
  ON public.address_index USING gin (normalized_address gin_trgm_ops);

CREATE TABLE IF NOT EXISTS public.address_alias_index (
  source_address_id  text PRIMARY KEY,
  address            text NOT NULL,
  normalized_address text NOT NULL CHECK (normalized_address <> ''),
  lot_code           text NOT NULL CHECK (lot_code ~ '^[0-9]{12}$'),
  lat                double precision NOT NULL CHECK (lat BETWEEN 3.6 AND 4.9),
  lng                double precision NOT NULL CHECK (lng BETWEEN -75.1 AND -73.9),
  address_type       smallint NOT NULL CHECK (address_type IN (2, 3))
);

ALTER TABLE public.address_alias_index ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS address_alias_normalized_prefix_idx
  ON public.address_alias_index (normalized_address text_pattern_ops);
CREATE INDEX IF NOT EXISTS address_alias_normalized_trgm_idx
  ON public.address_alias_index USING gin (normalized_address gin_trgm_ops);

CREATE OR REPLACE VIEW public.address_search_source
WITH (security_invoker = true)
AS
  SELECT
    source_address_id, address, normalized_address, lot_code,
    lat, lng, address_type, treatment, locality, neighborhood
  FROM public.address_index
  UNION ALL
  SELECT
    source_address_id, address, normalized_address, lot_code,
    lat, lng, address_type, NULL::text, NULL::text, NULL::text
  FROM public.address_alias_index;

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
  alias_imported_count  bigint NOT NULL DEFAULT 0,
  alias_source_count    bigint,
  alias_last_object_id  bigint NOT NULL DEFAULT 0,
  alias_completed_at    timestamptz,
  error                 text
);
ALTER TABLE public.address_index_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.address_index_meta
  ADD COLUMN IF NOT EXISTS alias_imported_count bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS alias_source_count bigint,
  ADD COLUMN IF NOT EXISTS alias_last_object_id bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS alias_completed_at timestamptz;
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
    FROM public.address_search_source AS ai
    WHERE ai.normalized_address = p_query
      AND EXISTS (
        SELECT 1 FROM public.address_index_meta AS meta
        WHERE meta.singleton AND meta.status = 'ready'
      )
    LIMIT 64
  ),
  prefix_candidates AS MATERIALIZED (
    SELECT ai.*, 2 AS match_class
    FROM public.address_search_source AS ai
    WHERE length(p_query) >= 3
      AND EXISTS (
        SELECT 1 FROM public.address_index_meta AS meta
        WHERE meta.singleton AND meta.status = 'ready'
      )
      AND ai.normalized_address LIKE p_query || '%'
      AND ai.normalized_address <> p_query
    -- Keep the indexed prefix scan bounded before optional proximity sorting.
    LIMIT 128
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
    FROM public.address_search_source AS ai
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
  ),
  lot_candidates AS MATERIALIZED (
    SELECT DISTINCT ON (c.lot_code) c.*
    FROM candidates AS c
    ORDER BY
      c.lot_code,
      c.match_class DESC,
      CASE
        WHEN p_lat IS NOT NULL AND p_lng IS NOT NULL
        THEN power(c.lat - p_lat, 2) + power(c.lng - p_lng, 2)
        ELSE NULL
      END ASC NULLS LAST,
      similarity(c.normalized_address, p_query) DESC,
      c.address_type ASC,
      c.address,
      c.source_address_id
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
  FROM lot_candidates AS c
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
    c.address_type ASC,
    c.address,
    c.lot_code
  LIMIT LEAST(GREATEST(COALESCE(p_limit, 8), 1), 8);
$$;

REVOKE ALL ON TABLE public.address_index FROM anon, authenticated;
REVOKE ALL ON TABLE public.address_alias_index FROM anon, authenticated;
REVOKE ALL ON TABLE public.address_search_source FROM anon, authenticated;
REVOKE ALL ON TABLE public.address_index_meta FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.address_index TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.address_alias_index TO service_role;
GRANT SELECT ON TABLE public.address_search_source TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.address_index_meta TO service_role;
REVOKE ALL ON FUNCTION public.search_address_index(
  text, double precision, double precision, text[], integer
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.search_address_index(
  text, double precision, double precision, text[], integer
) TO service_role;
