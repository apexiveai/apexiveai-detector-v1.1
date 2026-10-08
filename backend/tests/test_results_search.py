import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import main


class ResultsSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        report = self.root / 'job-id' / 'report'
        report.mkdir(parents=True)
        self.matches = [
            {
                'match_id': f'M-{index}',
                'application_a': application_a,
                'application_b': application_b,
                'source_a_asset_id': f'A-{index}',
                'source_b_asset_id': f'B-{index}',
                'source_a_file': 'government.pdf',
                'source_b_file': 'records.xlsx',
                'decision': decision,
            }
            for index, application_a, application_b, decision in (
                (1, 'T/2026/000001', '', 'EXACT_VISUAL_IDENTITY'),
                (2, '', 'T/2026/000002', 'HIGH_VISUAL_SIMILARITY'),
                (3, 'T/2026/000003', 'T/2026/000003', 'EXACT_VISUAL_IDENTITY'),
            )
        ]
        (report / 'matches.json').write_text(json.dumps(self.matches), encoding='utf8')
        self.data_patch = patch.object(main, 'DATA', self.root)
        self.jobs_patch = patch.dict(main.JOBS, {'job-id': {'status': 'completed'}})
        self.data_patch.start()
        self.jobs_patch.start()

    def tearDown(self):
        self.jobs_patch.stop()
        self.data_patch.stop()
        self.temp_dir.cleanup()

    def test_search_matches_application_number_on_either_source(self):
        result = main.results('job-id', q='t/2026/000002')

        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['application_b'], 'T/2026/000002')

    def test_search_and_decision_filter_are_paginated_after_filtering(self):
        result = main.results('job-id', offset=1, limit=1, q='t/2026', decision='EXACT_VISUAL_IDENTITY')

        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['match_id'], 'M-3')

    def test_search_can_find_text_only_mark_matches(self):
        self.matches[0]['source_a_text'] = 'Example Wordmark'
        self.matches[0]['decision'] = 'EXACT_TEXT_MARK_MATCH'
        (self.root / 'job-id' / 'report' / 'matches.json').write_text(json.dumps(self.matches), encoding='utf8')

        result = main.results('job-id', q='wordmark')
        exact = main.results('job-id', decision='EXACT')

        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['source_a_text'], 'Example Wordmark')
        self.assertEqual(exact['total'], 2)

    def test_extracted_mark_inventory_pages_all_six_thousand_assets(self):
        report = self.root / 'job-id' / 'report'
        assets = [
            {'asset_id': f'A-{index + 1:06d}', 'page': index + 1, 'image_file': f'A_{index + 1:06d}.png'}
            for index in range(6000)
        ]
        (report / 'assets_a.json').write_text(json.dumps(assets), encoding='utf8')

        first = main.assets('job-id', 'A')
        last = main.assets('job-id', 'A', offset=5900)

        self.assertEqual(first['total'], 6000)
        self.assertEqual(len(first['items']), 100)
        self.assertEqual(first['items'][0]['asset_id'], 'A-000001')
        self.assertEqual(last['items'][0]['asset_id'], 'A-005901')
        self.assertEqual(last['items'][-1]['asset_id'], 'A-006000')
        self.assertEqual(last['items'][-1]['asset_url'], '/api/jobs/job-id/assets/A/A_006000.png')


if __name__ == '__main__':
    unittest.main()
