"""
Tests for the letterhead importer.

The guarantees under test are the ones that make an imported letterhead
trustworthy: the detected band is the masthead and not the first speck of
toner, the crop and the reservation are the same number of millimetres so the
band prints back at 1:1, and the rendered source page a client is handed
between the two steps cannot be used to reach anyone else's file.

Run:  python3 -m unittest discover -s tests -v
"""

import os
import sys
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import letterhead_import as li  # noqa: E402

A4_H = 297.0
PAGE_PX = (1240, 1754)          # A4 at the importer's own render DPI


def page(bands, size=PAGE_PX, page_h_mm=A4_H, background="white"):
    """A synthetic page. `bands` are (top_mm, bottom_mm, colour) rectangles."""
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    for top_mm, bottom_mm, colour in bands:
        draw.rectangle(
            [60, int(top_mm / page_h_mm * size[1]),
             size[0] - 60, int(bottom_mm / page_h_mm * size[1])],
            fill=colour,
        )
    return image


class Detection(unittest.TestCase):
    """Finding where the masthead ends."""

    def test_masthead_ends_at_the_first_real_gap(self):
        # Logo block 12-38mm, then whitespace, then the document title.
        found = li.detect_masthead_mm(
            page([(12, 38, "black"), (55, 62, "black")]), A4_H)
        self.assertAlmostEqual(40, found, delta=3)

    def test_pale_coloured_band_counts_as_ink(self):
        # A tinted masthead is nowhere near black. Measuring against the paper
        # rather than against black is what catches it.
        found = li.detect_masthead_mm(
            page([(0, 45, (222, 235, 247))]), A4_H)
        self.assertAlmostEqual(46, found, delta=3)

    def test_speck_near_the_top_does_not_end_the_band(self):
        # A scanner speck 3mm down must not be mistaken for the whole masthead.
        found = li.detect_masthead_mm(
            page([(3, 4, "black"), (18, 40, "black"), (60, 66, "black")]), A4_H)
        self.assertGreater(found, li.MIN_BAND_MM)
        self.assertAlmostEqual(42, found, delta=3)

    def test_blank_page_detects_nothing(self):
        # None, not a guess: the caller says so in the UI rather than
        # presenting a default as if it had been measured.
        self.assertIsNone(li.detect_masthead_mm(page([]), A4_H))

    def test_page_with_no_gap_is_capped_not_runaway(self):
        # A header running straight into the table has no gap to find.
        crowded = [(10, 40, "black")] + [(45 + i * 4, 47 + i * 4, "black")
                                         for i in range(25)]
        found = li.detect_masthead_mm(page(crowded), A4_H)
        self.assertLessEqual(found, li.bounds_mm(A4_H)[1])
        self.assertGreater(found, li.MIN_BAND_MM)

    def test_detection_is_independent_of_render_resolution(self):
        # The same page at half the pixels must give the same millimetres,
        # because millimetres are what gets stored and printed.
        bands = [(12, 38, "black"), (55, 62, "black")]
        big = li.detect_masthead_mm(page(bands), A4_H)
        small = li.detect_masthead_mm(page(bands, size=(620, 877)), A4_H)
        self.assertAlmostEqual(big, small, delta=2)


class CropAndReservation(unittest.TestCase):
    """The crop and the reserved space must be the same band."""

    def test_crop_height_matches_the_millimetres_returned(self):
        image = page([(0, 40, "black")])
        crop, mm = li.crop_masthead(image, A4_H, 40)
        self.assertEqual(40.0, mm)
        self.assertAlmostEqual(40.0, crop.height / image.height * A4_H, delta=0.2)

    def test_crop_keeps_the_full_page_width(self):
        # Full width and starting at y=0 is what lets the band be printed back
        # edge to edge at 1:1.
        image = page([(0, 40, "black")])
        crop, _ = li.crop_masthead(image, A4_H, 40)
        self.assertEqual(image.width, crop.width)

    def test_millimetres_are_whole_numbers(self):
        # clients.tpl_letterhead_mm is an integer column; a fractional crop
        # would be reserved at a different height and print stretched.
        for requested in (40.4, 40.6, 12.5, 132.9):
            _, mm = li.crop_masthead(page([(0, 60, "black")]), A4_H, requested)
            self.assertEqual(mm, float(int(mm)), f"{requested} -> {mm}")

    def test_out_of_range_requests_are_clamped_not_rejected(self):
        image = page([(0, 60, "black")])
        low, high = li.bounds_mm(A4_H)
        self.assertEqual(low, li.crop_masthead(image, A4_H, -50)[1])
        self.assertEqual(high, li.crop_masthead(image, A4_H, 9999)[1])
        self.assertEqual(low, li.crop_masthead(image, A4_H, "nonsense")[1])

    def test_bounds_are_whole_millimetres(self):
        for page_h in (A4_H, 279.4, 420.0, 100.0):
            low, high = li.bounds_mm(page_h)
            self.assertEqual(high, float(int(high)))
            self.assertGreaterEqual(high, low)

    def test_detected_value_is_always_croppable(self):
        # Whatever detection proposes must survive crop_masthead unchanged,
        # or the client would be shown one number and given another.
        image = page([(12, 38, "black"), (55, 62, "black")])
        found = li.detect_masthead_mm(image, A4_H)
        self.assertEqual(found, li.crop_masthead(image, A4_H, found)[1])


class Loading(unittest.TestCase):
    """What may be uploaded."""

    def _png(self, image):
        import io
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def test_image_upload_is_measured_by_its_own_aspect_ratio(self):
        # A bare image carries no page box. Assuming A4 *width* and deriving
        # the height keeps rows mapping to millimetres proportionally.
        blob = self._png(Image.new("RGB", (1000, 2000), "white"))
        _, w_mm, h_mm = li.render_first_page(blob, "scan.png")
        self.assertAlmostEqual(li.A4_W_MM, w_mm, places=3)
        self.assertAlmostEqual(li.A4_W_MM * 2, h_mm, places=3)

    def test_unknown_extension_is_refused(self):
        with self.assertRaises(li.LetterheadImportError):
            li.render_first_page(b"whatever", "invoice.docx")

    def test_empty_and_oversized_files_are_refused(self):
        with self.assertRaises(li.LetterheadImportError):
            li.render_first_page(b"", "invoice.png")
        with self.assertRaises(li.LetterheadImportError):
            li.render_first_page(b"x" * (li.MAX_IMPORT_BYTES + 1), "invoice.png")

    def test_corrupt_image_raises_a_message_the_client_can_act_on(self):
        with self.assertRaises(li.LetterheadImportError):
            li.render_first_page(b"not an image at all", "invoice.png")

    def test_corrupt_pdf_raises_a_message_the_client_can_act_on(self):
        with self.assertRaises(li.LetterheadImportError):
            li.render_first_page(b"%PDF-1.4 truncated", "invoice.pdf")


class SourceOwnership(unittest.TestCase):
    """The rendered page handed to the browser between the two steps.

    Its name comes back from the client on commit, so it is untrusted input.
    """

    def test_a_clients_own_source_is_accepted(self):
        name = li.source_name(7, "a" * 32)
        self.assertTrue(li.source_is_mine(name, 7))
        self.assertTrue(li.source_is_mine(name, "7"))

    def test_another_clients_source_is_refused(self):
        self.assertFalse(li.source_is_mine(li.source_name(8, "a" * 32), 7))

    def test_traversal_and_junk_are_refused(self):
        for name in ("../../app.py",
                     "/etc/passwd",
                     "import-7-" + "a" * 32 + ".png/../../app.py",
                     "import-7-zzzz.png",
                     "import-7-" + "a" * 32 + ".py",
                     "7-" + "a" * 32 + ".png",     # a committed letterhead
                     "", None, 0):
            self.assertFalse(li.source_is_mine(name, 7), name)

    def test_a_directory_prefix_cannot_smuggle_another_clients_id(self):
        # os.path.basename is applied before matching, so a path that ends in
        # this client's own name is still only ever that name.
        self.assertFalse(
            li.source_is_mine("import-7-" + "a" * 32 + ".png/x", 7))


if __name__ == "__main__":
    unittest.main()
