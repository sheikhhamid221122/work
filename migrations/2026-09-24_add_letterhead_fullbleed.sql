-- Migration: render an imported letterhead edge to edge
-- Date: 2026-09-24
--
-- The letterhead importer crops the top band of the client's own invoice,
-- full page width, starting at the physical top of the sheet. To reproduce
-- that at 1:1 the image has to be printed across the whole page -- outside
-- the invoice's side and top margins -- rather than inside the content box.
--
-- An uploaded letterhead is a different thing: it is a graphic the client
-- prepared for us, with no relationship to the page edges, and it has always
-- been centred inside the content box. Changing that for existing clients
-- would silently resize a letterhead they have already approved.
--
-- So the two behaviours live side by side and this column picks between them.
--
-- SAFETY: DEFAULT false, so every existing row -- and every future manual
-- upload -- keeps the current inset rendering exactly. Only the importer sets
-- it true, and only for an image it cropped itself.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS tpl_letterhead_fullbleed BOOLEAN DEFAULT false;

COMMENT ON COLUMN clients.tpl_letterhead_fullbleed IS
  'true = letterhead image spans the full page (imported crop); false = inset in the content box (manual upload)';
