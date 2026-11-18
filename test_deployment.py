"""Read-only sample export checks; no network, server, credentials or user data."""
import tempfile, unittest
from pathlib import Path
from html.parser import HTMLParser
from deploy.build_static import build
class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.tags=[]
    def handle_starttag(self,tag,attrs):
        self.tags.append(tag);self.links.extend(v for k,v in attrs if k=='href')
class StaticDemoTest(unittest.TestCase):
    def test_allowlist_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'demo';build(out)
            self.assertEqual({f.name for f in out.iterdir()},{'index.html','A-lantern.pdf','B-cloud.pdf'})
            page=(out/'index.html').read_text(); parser=Links();parser.feed(page)
            self.assertEqual(parser.links,['A-lantern.pdf#page=1','B-cloud.pdf#page=2'])
            self.assertTrue({'script','form','iframe','input'}.isdisjoint(parser.tags))
            self.assertIn('未找到该词的证据',page)
            with self.assertRaises(FileExistsError):build(out)
