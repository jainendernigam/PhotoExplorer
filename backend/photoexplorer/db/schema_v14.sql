-- Version 14: each photo's main colour (0xRRGGBB), shown while its thumbnail loads.
-- Filled when a photo is scanned; older photos are filled from their thumbnails
-- in the background (scanner/colors.py).
ALTER TABLE media ADD COLUMN color INTEGER;
