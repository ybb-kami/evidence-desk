import unittest
import workbench
from direct_measurement import extract
from question_parts import combine, describe_gap, split_question

def hit(page,text):
    return dict(id='f'*64,page=page,block=1,part=1,text=text)

class FieldTests(unittest.TestCase):
    def test_two_different_fields_and_coordinated_title(self):
        self.assertEqual(split_question('What was the write speed, and what was the signal amplitude?'),
                         ['What was the write speed','what was the signal amplitude'])
        self.assertEqual(len(split_question('How many images were collected for Research and Development?')),1)

    def test_only_common_condition_can_yield_a_reading(self):
        data=[hit(1,'The optical power during cooling was 26 mW. The optical power during heating was 43 mW.')]
        claim,reason=extract('What was the optical power during heating?','',data)
        self.assertIsNone(reason)
        self.assertEqual(claim['text'],'43 mW')
        claim,reason=extract('What was the optical power?','',data)
        self.assertIsNone(claim)
        self.assertEqual(reason,'ambiguous_measurement')
        self.assertIsNone(extract('What was the camera exposure duration?','',data)[0])

    def test_fractional_scientific_units_and_no_identifier_standalone(self):
        data=[hit(1,'The sensor sampling frequency was 1.25e-3 MHz.')]
        claim,_=extract('What was the sensor sampling frequency?','',data)
        self.assertEqual(claim['text'],'1.25e-3 MHz')
        data=[hit(1,'The sensor sampling frequency is plotted in Figure 24.')]
        self.assertIsNone(extract('What was the sensor sampling frequency?','',data)[0])

    def test_converted_and_changed_units_need_explicit_source_support(self):
        from measurement_units import supports_units
        self.assertTrue(supports_units("时延为5毫秒","Response latency was 5 ms."))
        self.assertFalse(supports_units("时延为5秒","Response latency was 5 ms."))
        self.assertFalse(supports_units("Capacity was 12 GB","Capacity reached 12 GiB."))
        self.assertTrue(supports_units("Capacity was 12 GiB","Capacity reached 12 GiB."))
        self.assertTrue(supports_units("at 12.00 mW","12 mW"))
        self.assertFalse(supports_units("at 12 MW","12 mW"))
        from grounded_draft import _numbers
        self.assertEqual(_numbers("5.0 percent"), _numbers("5%"))
        self.assertNotEqual(_numbers("5 percentage points"),_numbers("5%"))
        self.assertEqual(_numbers("3.0e-3"),_numbers("0.003"))
        self.assertNotEqual(_numbers("-3"),_numbers("3"))

    def test_merge_keys_point_at_independent_physical_pages(self):
        first,last=hit(1,'Power measurement'),hit(2,'Frequency measurement')
        def answer(row):
            return dict(answer='confirmed',hits=[row],generation={'claims':[dict(row,span=row['text'],hit_index=0)]})
        result=combine([('power?',answer(first)),('frequency?',answer(last))])
        self.assertEqual(result['completeness'],'complete')
        self.assertEqual([(c['page'],r['page']) for c,r in zip(result['generation']['claims'],result['hits'])],[(1,1),(2,2)])
        self.assertIn('序列号',describe_gap('Which serial number was assigned?', [first]))

if __name__=='__main__':
    unittest.main()
