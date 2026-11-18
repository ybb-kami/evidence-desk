import tempfile,unittest
from pathlib import Path
from tools.official_manifest import NAMES,manifest
class ManifestTest(unittest.TestCase):
    def test_missing_and_mislabeled_are_not_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); sample=(Path(__file__).parent/'sample.pdf').read_bytes()
            (root/NAMES[2]).write_bytes(sample);(root/NAMES[3]).write_bytes(sample)
            r=manifest(root)
            self.assertEqual([c['status'] for c in r['items']],['missing','missing','scan_has_sampled_text','unexpected_page_count'])
            self.assertFalse(r['source_verified']);self.assertFalse(r['deployment_validated'])
