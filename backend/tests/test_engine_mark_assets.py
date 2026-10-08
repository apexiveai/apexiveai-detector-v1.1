import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from openpyxl import Workbook
from openpyxl.drawing.image import Image as SpreadsheetImage
from PIL import Image, ImageDraw

from app.engine import (
    cv,
    pdf_assets,
    prepare_verification,
    run,
    verify,
    verify_prepared,
    xlsx_assets,
)


class MarkAssetExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        image = Image.new('RGB', (40, 30), 'white')
        drawing = ImageDraw.Draw(image)
        drawing.ellipse((3, 2, 22, 28), fill='black')
        drawing.rectangle((21, 9, 37, 21), fill='black')
        self.image_path = self.root / 'logo.png'
        image.save(self.image_path)
        self.image_bytes = self.image_path.read_bytes()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pdf_extracts_only_images_in_the_540_field(self):
        pdf_path = self.root / 'sample.pdf'
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 24), 'Application No. T/2026/001111')
        page.insert_text((20, 40), '(540)')
        page.insert_image(pymupdf.Rect(120, 50, 200, 100), stream=self.image_bytes)
        page.insert_text((20, 140), '(550)')
        page.insert_image(pymupdf.Rect(120, 180, 200, 230), stream=self.image_bytes)
        doc.save(pdf_path)
        doc.close()

        assets = pdf_assets(pdf_path, self.root, lambda *_: None)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['field_code'], '(540)')
        self.assertEqual(assets[0]['extract_method'], 'pdf_540_embedded_image')
        self.assertEqual(assets[0]['application_no'], 'T/2026/001111')
        self.assertTrue((self.root / assets[0]['image_file']).is_file())

    def test_pdf_logo_fields_keep_their_own_application_numbers(self):
        pdf_path = self.root / 'multiple_records.pdf'
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 24), 'Application No. T/2026/001111')
        page.insert_text((20, 40), '(540)')
        page.insert_image(pymupdf.Rect(120, 50, 200, 100), stream=self.image_bytes)
        page.insert_text((20, 124), 'Application No. T/2026/002222')
        page.insert_text((20, 140), '(540)')
        page.insert_image(pymupdf.Rect(120, 170, 200, 220), stream=self.image_bytes)
        doc.save(pdf_path)
        doc.close()

        assets = pdf_assets(pdf_path, self.root, lambda *_: None)

        self.assertEqual([asset['application_no'] for asset in assets], [
            'T/2026/001111',
            'T/2026/002222',
        ])

    def test_pdf_extracts_text_only_marks_from_the_540_field(self):
        pdf_path = self.root / 'text_mark.pdf'
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 40), '(540)')
        page.insert_text((120, 70), 'TEXT WORDMARK')
        page.insert_text((20, 140), '(550)')
        doc.save(pdf_path)
        doc.close()

        assets = pdf_assets(pdf_path, self.root, lambda *_: None)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['mark_text'], 'TEXT WORDMARK')
        self.assertEqual(assets[0]['mark_content_type'], 'text')
        self.assertEqual(assets[0]['field_label'], '(540) text mark')
        self.assertEqual(assets[0]['extract_method'], 'pdf_540_text_mark')
        self.assertTrue((self.root / assets[0]['image_file']).is_file())

    def test_pdf_extracts_marks_from_a_thousand_pages(self):
        pdf_path = self.root / 'thousand_pages.pdf'
        doc = pymupdf.open()
        for index in range(1000):
            page = doc.new_page(width=300, height=300)
            page.insert_text((20, 40), '(540)')
            page.insert_image(pymupdf.Rect(120, 50, 200, 100), stream=self.image_bytes)
            page.insert_text((20, 140), f'(550) {index + 1}')
        doc.save(pdf_path)
        doc.close()

        assets = pdf_assets(pdf_path, self.root, lambda *_: None)

        self.assertEqual(len(assets), 1000)
        self.assertEqual(assets[0]['page'], 1)
        self.assertEqual(assets[-1]['page'], 1000)

    def test_xlsx_extracts_only_images_anchored_in_mark_column(self):
        xlsx_path = self.root / 'sample.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Application No.', 'Mark', 'Notes'])
        sheet.append(['T/2026/002509', 'Example mark', 'not a logo'])
        mark_image = SpreadsheetImage(str(self.image_path))
        mark_image.anchor = 'B2'
        sheet.add_image(mark_image)
        unrelated_image = SpreadsheetImage(str(self.image_path))
        unrelated_image.anchor = 'C2'
        sheet.add_image(unrelated_image)
        workbook.save(xlsx_path)
        workbook.close()

        assets = xlsx_assets(xlsx_path, self.root, lambda *_: None)

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['column'], 2)
        self.assertEqual(assets[0]['mark'], 'Example mark')
        self.assertEqual(assets[0]['field_label'], 'Mark column logo')

    def test_xlsx_extracts_image_and_text_marks_from_every_mark_sheet(self):
        xlsx_path = self.root / 'multiple_sheets.xlsx'
        workbook = Workbook()
        first = workbook.active
        first.title = 'First'
        first['A1'] = 'Application No.'
        first['E1'] = 'Main objectives'
        first['A2'] = 'T/2026/000001'
        first['E2'] = 'Text mark'
        first['A3'] = 'T/2026/000002'
        first.add_image(SpreadsheetImage(str(self.image_path)), 'E3')
        second = workbook.create_sheet('Second')
        second.append(['Application No.', 'Mark'])
        second.append(['T/2026/000003', 'Second sheet text'])
        workbook.save(xlsx_path)
        workbook.close()

        assets = xlsx_assets(xlsx_path, self.root, lambda *_: None)

        self.assertEqual([(asset['sheet'], asset['row']) for asset in assets], [
            ('First', 2),
            ('First', 3),
            ('Second', 2),
        ])
        self.assertEqual(assets[0]['mark_content_type'], 'text')
        self.assertEqual(assets[0]['mark_text'], 'Text mark')
        self.assertEqual(assets[1]['mark_content_type'], 'image')
        self.assertEqual(assets[2]['mark_text'], 'Second sheet text')

    def test_prepared_verification_preserves_existing_match_result(self):
        image = cv(self.image_path)

        self.assertEqual(
            verify(image, image),
            verify_prepared(prepare_verification(image), prepare_verification(image)),
        )

    def test_xlsx_with_images_but_no_mark_header_reports_the_problem(self):
        xlsx_path = self.root / 'unlabeled.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Name', 'Notes'])
        sheet.append(['Example', 'not a logo'])
        image = SpreadsheetImage(str(self.image_path))
        image.anchor = 'B2'
        sheet.add_image(image)
        workbook.save(xlsx_path)
        workbook.close()

        with self.assertRaisesRegex(ValueError, 'has no Mark/logo/device column'):
            xlsx_assets(xlsx_path, self.root, lambda *_: None)

    def test_xlsx_reports_vector_images_that_cannot_be_rendered(self):
        xlsx_path = self.root / 'unrenderable.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Application No.', 'Mark'])
        sheet.append(['T/2026/002509', 'Example mark'])
        workbook.save(xlsx_path)
        workbook.close()

        with (
            patch('app.engine.ooxml_images', return_value=[('Sheet', 2, 2, b'wmf-data', '.wmf')]),
            patch('app.engine.render_vector_bytes', return_value=None),
        ):
            with self.assertRaisesRegex(ValueError, 'Install ImageMagick or Inkscape'):
                xlsx_assets(xlsx_path, self.root, lambda *_: None)

    def test_pdf_to_xlsx_logo_match_works_in_either_upload_order(self):
        pdf_path = self.root / 'government.pdf'
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 40), '(540)')
        page.insert_image(pymupdf.Rect(120, 50, 200, 110), filename=str(self.image_path))
        page.insert_text((20, 140), '(550)')
        doc.save(pdf_path)
        doc.close()

        xlsx_path = self.root / 'office.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Application No.', 'Mark'])
        sheet.append(['T/2026/002509', 'Example mark'])
        sheet.add_image(SpreadsheetImage(str(self.image_path)), 'B2')
        workbook.save(xlsx_path)
        workbook.close()

        for index, (source_a, source_b, field_a, field_b) in enumerate((
            (pdf_path, xlsx_path, '(540) logo', 'Mark column logo'),
            (xlsx_path, pdf_path, 'Mark column logo', '(540) logo'),
        )):
            job_dir = self.root / f'job-{index}'
            summary = run(job_dir, source_a, source_b, lambda *_: None)
            matches = json.loads((job_dir / 'report' / 'matches.json').read_text())

            self.assertEqual(summary['matched'], 1)
            self.assertEqual(matches[0]['source_a_field'], field_a)
            self.assertEqual(matches[0]['source_b_field'], field_b)

    def test_pdf_to_pdf_keeps_source_assets_separate(self):
        pdf_paths = [self.root / 'first.pdf', self.root / 'second.pdf']
        for index, pdf_path in enumerate(pdf_paths):
            doc = pymupdf.open()
            page = doc.new_page(width=300, height=300)
            page.insert_text((20, 24), f'Application No. T/2026/00000{index + 1}')
            page.insert_text((20, 40), '(540)')
            page.insert_image(pymupdf.Rect(120, 50, 200, 100), stream=self.image_bytes)
            doc.save(pdf_path)
            doc.close()

        job_dir = self.root / 'pdf-pdf-job'
        summary = run(job_dir, pdf_paths[0], pdf_paths[1], lambda *_: None)
        matches = json.loads((job_dir / 'report' / 'matches.json').read_text())
        assets_a = json.loads((job_dir / 'report' / 'assets_a.json').read_text())
        assets_b = json.loads((job_dir / 'report' / 'assets_b.json').read_text())

        self.assertEqual(summary['matched'], 1)
        self.assertNotEqual(assets_a[0]['image_file'], assets_b[0]['image_file'])
        self.assertTrue((job_dir / 'assets' / assets_a[0]['image_file']).is_file())
        self.assertTrue((job_dir / 'assets' / assets_b[0]['image_file']).is_file())
        self.assertIn('/assets/A/', matches[0]['asset_a_url'])
        self.assertIn('/assets/B/', matches[0]['asset_b_url'])

    def test_text_only_pdf_and_workbook_marks_match(self):
        pdf_path = self.root / 'text_mark.pdf'
        doc = pymupdf.open()
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 40), '(540)')
        page.insert_text((120, 70), 'Example Wordmark')
        page.insert_text((20, 140), '(550)')
        doc.save(pdf_path)
        doc.close()

        xlsx_path = self.root / 'text_mark.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['Application No.', 'Mark'])
        sheet.append(['T/2026/002509', 'Example Wordmark'])
        workbook.save(xlsx_path)
        workbook.close()

        job_dir = self.root / 'text-job'
        summary = run(job_dir, pdf_path, xlsx_path, lambda *_: None)
        matches = json.loads((job_dir / 'report' / 'matches.json').read_text())

        self.assertEqual(summary['matched'], 1)
        self.assertEqual(matches[0]['decision'], 'EXACT_TEXT_MARK_MATCH')
        self.assertEqual(matches[0]['source_a_text'], 'Example Wordmark')
        self.assertIsNone(matches[0]['asset_b_url'])


if __name__ == '__main__':
    unittest.main()
