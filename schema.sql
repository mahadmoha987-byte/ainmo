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
  decree_version text NOT NULL DEFAULT 'D.555/2021',
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
