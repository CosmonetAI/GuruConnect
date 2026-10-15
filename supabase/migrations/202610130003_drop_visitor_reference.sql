-- Migration: drop visitor_reference column from walkin_visits
-- Removes the Visitor ID / reference number column now managed out of the system

alter table if exists public.walkin_visits
  drop column if exists visitor_reference;
