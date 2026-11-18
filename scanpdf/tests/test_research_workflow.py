import io
import json
import unittest
from unittest.mock import Mock, patch

import workbench
from app import Bad
from local_generation import LocalGenerator
from model_stream import answer_prefix, read_completion
from research_workflow import choices, execute, report, source, validate


DOCID = 'a' * 64
DOC = dict(id=DOCID, pages=2, textless_pages=[2], name='experiment.pdf',
           chunks=[dict(page=1, block=3, part=1, text='The validation split contains 37 samples. The test split has 93 samples.')])


def frame(obj):
    return b'data: ' + json.dumps(obj).encode() + b'\n\n'


def event(delta, reason=None):
    return frame({'choices': [{'delta': delta, 'finish_reason': reason}]})


class StreamTests(unittest.TestCase):
    def test_reads_only_answer_field_during_generation(self):
        parts = ['{"status":"answered","answer":"实', '验包含', ' 37 ', '个样本。","source":1,', '"quote":"37 samples"}']
        body = event({'role':'assistant','reasoning_content':'private scratchpad'})
        body += b''.join(event({'content':delta}) for delta in parts)
        body += event({}, 'stop') + frame({'choices':[],'usage':{}}) + b'data: [DONE]\n\n'
        seen=[]
        result = read_completion(io.BytesIO(body), seen.append)
        self.assertEqual(''.join(seen), '实验包含 37 个样本。')
        self.assertGreater(len(seen), 1)
        self.assertEqual(json.loads(result['choices'][0]['message']['content'])['source'], 1)

    def test_pdf_quote_normalization_retains_source_offsets_and_rejects_ambiguous_match(self):
        from quoted_span import bind
        raw = "Our classiﬁer compares inter-\nnational samples and carries a −5 bias."
        a,b = bind("classifier compares international samples",raw)
        self.assertEqual(raw[a:b],"classiﬁer compares inter-\nnational samples")
        self.assertIsNone(bind("completely different",raw))
        self.assertIsNone(bind("50", "The two plate counts were 50 and 50."))

    def test_incomplete_and_oversize_streams_are_rejected(self):
        for body in [event({'content':'{"answer":"in progress'}), b'data: [DONE]\n\n',
                     b'data: '+b'x'*20000+b'\n\n', frame({'error': 'unavailable'})]:
            with self.subTest(length=len(body)), self.assertRaises((ValueError, KeyError)):
                read_completion(io.BytesIO(body), lambda _: None)

    def test_does_not_confuse_a_quoted_key_or_a_split_escape(self):
        self.assertEqual(answer_prefix('{"quote":"\\"answer\\":\\"secret\\""'), '')
        prefix = '{"quote":"first","answer":"a'
        self.assertEqual(answer_prefix(prefix+'\\u00'), 'a')
        self.assertEqual(answer_prefix(prefix+'\\ud83d'), 'a')
        self.assertEqual(answer_prefix(prefix+'\\ud83d\\ude80'), 'a🚀')
        self.assertEqual(answer_prefix(prefix+'\\\\'), 'a\\')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.req=dict(ids=[DOCID],question='What price was paid?',model_planner=False,publisher_consent=False)
        self.store=Mock()
        self.store.records.return_value=[DOC]
        self.store.local_generator.model='fixture'

    def test_scope_rejects_archives_duplicates_and_unapproved_remote_arguments(self):
        validate(self.store, self.req)
        for req in (dict(self.req, ids=[DOCID,DOCID]),dict(self.req, url='http://anywhere'),
                    dict(self.req, publisher_consent=True),dict(self.req, model_planner=1)):
            with self.subTest(req=req), self.assertRaises(Bad):
                validate(self.store, req)
        self.store.records.return_value=[]
        with self.assertRaises(Bad):
            validate(self.store,self.req)

    def test_a_missing_answer_and_unindexed_page_add_an_actual_tool(self):
        events=[]
        with patch('followup.answer_question', return_value={'hits':[],'answer':None,'gap':'找不到依据'}):
            execute(self.store, [DOC], self.req, None, lambda k,x: events.append((k,x)))
        self.assertEqual([e['tool'] for k,e in events if k=='receipt'],['answer_paper','scan_coverage'])
        self.assertEqual([e['tool'] for k,e in events if k=='replan'],['scan_coverage'])
        groups=events[-1][1]['reports']
        self.assertFalse(groups[0]['answer'])
        self.assertEqual(groups[1]['scan'],[{'id':DOCID,'page':2}])
        self.store.local_generator.choose_tools.assert_not_called()

    def test_wrong_document_or_unsupported_generated_span_cannot_be_cited(self):
        h=dict(DOC['chunks'][0],id=DOCID)
        self.assertIsNone(source(DOC,dict(h,id='b'*64)))
        checked=source(DOC,h,quote='37 samples',start=0,end=2)
        self.assertEqual(h['text'][checked['start']:checked['end']], '37 samples')
        bad=report(DOC,{'answer':'made up','hits':[h],'generation':{'claims':[dict(h,span='missing string')]}})
        self.assertIsNone(bad['answer'])

    def test_genuine_provider_tool_calls_only_and_no_arguments_from_pdf(self):
        model=LocalGenerator('http://127.0.0.1:12508/v1/chat/completions','test')
        req=dict(self.req,question='Check the citation graph')
        _,offered=choices([DOC],req['question'],None)
        func={'type':'function','function':{'name':'citation_graph','arguments':'{}'}}
        def reply(calls):
            return {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':calls}}]}
        with patch.object(model,'_request',return_value=reply([func])) as send:
            self.assertEqual(model.choose_tools(req['question'],1,offered),('model_tool_calls',['citation_graph']))
            payload=send.call_args.args[0]
            self.assertEqual(payload['tool_choice'],'auto')
            self.assertNotIn(DOC['chunks'][0]['text'],str(payload))
        for calls in ([],[dict(func,function={'name':'publication_metadata','arguments':'{}'})],
                      [dict(func,function={'name':'citation_graph','arguments':'{"url":"forbidden"}'})]):
            with self.subTest(calls=calls),patch.object(model,'_request',return_value=reply(calls)):
                self.assertEqual(model.choose_tools(req['question'],1,offered),('invalid',[]))

    def test_number_in_unrelated_sentence_cannot_support_claim(self):
        model=LocalGenerator('http://127.0.0.1:12508/v1/chat/completions','test')
        hit=dict(DOC['chunks'][0],id=DOCID)
        result={'status':'answered','source':1,'quote':'37 samples','answer':'93 samples'}
        with patch.object(model,'_request',return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result)}}]}):
            self.assertEqual(model.answer('How many?', [hit])['status'],'validation_failed')


if __name__ == '__main__':
    unittest.main()
