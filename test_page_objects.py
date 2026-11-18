import unittest,fitz
from page_objects import inspect_page
class PageObjectTest(unittest.TestCase):
    def test_coordinates_not_guessed_cells(self):
        with fitz.open() as pdf:
            p=pdf.new_page();p.insert_text((40,70),'Table 1: An original inventory across two locations with aligned values.')
            p.insert_text((50,100),'North');p.insert_text((210,100),'12');p.insert_text((50,125),'South');p.insert_text((210,125),'18')
            data=pdf.tobytes()
        result=inspect_page(data,1)
        self.assertEqual(result['captions'][0]['kind'],'table')
        self.assertIn(['South','18'],[[v['text'] for v in row['fragments']] for row in result['table_rows']])
        self.assertEqual(result['images'],[])
        with self.assertRaises(ValueError):inspect_page(data,4)

from test_acceptance import LocalHTTPFixture
from urllib.error import HTTPError
class PageObjectHTTPTest(LocalHTTPFixture, unittest.TestCase):
    def test_open_boundaries_over_http(self):
        doc=self.import_pdf('teaching.pdf')['doc']
        info=self.read_json('/api/page-objects/'+doc['id']+'/1')
        self.assertEqual(info['page'],1)
        self.assertEqual(len(info['size']),2)
        for path,code in [('/api/page-objects/'+doc['id']+'/650',400),('/api/page-objects/'+'0'*64+'/1',404)]:
            with self.subTest(path=path),self.assertRaises(HTTPError) as err:self.read_json(path)
            self.assertEqual(err.exception.code,code);err.exception.close()
