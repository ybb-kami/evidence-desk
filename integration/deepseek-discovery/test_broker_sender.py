import unittest
from unittest.mock import Mock

from broker_sender import approved_sender
from group_budget import Denied, ENDPOINT


def catalog(badge):
    return {'data': [{'id': 'deepseek-flash', 'name': 'DeepSeek-V4.1-Flash',
                      'client_metadata': {'deepseek-harness': {'badge': {'text': badge}}}}]}


class SenderTest(unittest.TestCase):
    def test_exact_model_before_one_send(self):
        get = Mock(return_value=catalog({'en': '1017'}))
        post = Mock(return_value={'choices': []})
        send = approved_sender('laboratory-placeholder', catalog=get, post_factory=lambda _: post)
        self.assertEqual(send(ENDPOINT['model'], b'{}'), {'choices': []})
        get.assert_called_once()
        post.assert_called_once_with(ENDPOINT['model'], b'{}')

    def test_directory_failures_never_chat(self):
        for directory in [catalog('1020'), catalog(None), {'error': 'upstream'}, {'data': []},
                          {'data': catalog('1017')['data'] * 2}]:
            post = Mock()
            send = approved_sender('laboratory-placeholder', catalog=lambda _: directory, post_factory=lambda _: post)
            with self.assertRaises(Denied):
                send(ENDPOINT['model'], b'{}')
            post.assert_not_called()
    def test_tools_exact_route_and_failure_without_retry(self):
        get = Mock()
        post = Mock(side_effect=TimeoutError)
        send = approved_sender('laboratory-placeholder', catalog=get, post_factory=lambda _: post)
        for endpoint, body in [('http://invalid.example', b'{}'), (ENDPOINT['fetch'], b'laboratory-placeholder')]:
            with self.assertRaises(Denied):
                send(endpoint, body)
        with self.assertRaises(TimeoutError):
            send(ENDPOINT['search'], b'{}')
        post.assert_called_once_with(ENDPOINT['search'], b'{}')
        get.assert_not_called()
