-- Migration: drop customer_type and visitor_count columns from walkin_visits
-- Removes Customer type and Accompanying visitors columns per UI change request

alter table if exists public.walkin_visits
  drop column if exists customer_type,
  drop column if exists visitor_count;
