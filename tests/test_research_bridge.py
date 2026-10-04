import json
import subprocess
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from garra.research.service import ResearchService, ResearchUnavailable
from garra.research.worker import bound_requests, graph_sources
from garra.ui.service import InputError


class ResearchBridgeTests(unittest.TestCase):
    def test_rejects_url_document_paths_and_oversized_queries(self):
        service = ResearchService()
        with patch('garra.research.service.subprocess.run') as run:
            for body in ({'query': 'https://example.test'}, {'query': 'name\nprivate'},
                         {'query': 'a' * 201}, {'query': 'Marfan', 'limit': True},
                         {'query': 'Marfan', 'file': '/private/secret'}):
                with self.assertRaises(InputError):
                    service.search(body)
            run.assert_not_called()

    def test_completed_cache_is_copy_and_partial_errors_are_not_cached(self):
        service = ResearchService()
        value = {'status': 'ok', 'sources': [{'title': 'Verified record'}], 'cached': False}
        with patch('garra.research.service.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(value))) as run:
            first = service.search({'query': 'Marfan'})
            first['sources'][0]['title'] = 'Modified by caller'
            second = service.search({'query': 'Marfan'})
            self.assertEqual(second['sources'][0]['title'], 'Verified record')
            self.assertTrue(second['cached'])
            self.assertEqual(run.call_count, 1)
            self.assertFalse(run.call_args.kwargs.get('shell', False))
        value['status'] = 'partial'
        with patch('garra.research.service.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(value))) as run:
            service.search({'query': 'Other condition'})
            service.search({'query': 'Other condition'})
            self.assertEqual(run.call_count, 2)

    def test_failed_workers_and_timeouts_are_retryable_not_empty(self):
        service = ResearchService()
        for process in (SimpleNamespace(returncode=1, stdout=''),
                        SimpleNamespace(returncode=0, stdout='not json'),
                        SimpleNamespace(returncode=0, stdout='{"status":"unavailable"}')):
            with patch('garra.research.service.subprocess.run', return_value=process):
                with self.assertRaises(ResearchUnavailable):
                    service.search({'query': 'Marfan'})
        with patch('garra.research.service.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 65)):
            with self.assertRaises(ResearchUnavailable):
                service.search({'query': 'Marfan'})
        self.assertTrue(service._slots.acquire(blocking=False))

    def test_graph_merge_retains_contact_urls_and_descriptions(self):
        graph = {'focus': ['MONDO:1'], 'edges': [{'from':'MONDO:1','to':'ORPHANET.EC:1','relation':'expert_centre'}], 'nodes': [
            {'id': 'MONDO:1', 'label': 'Condition', 'kind': 'disease', 'info': {}},
            {'id': 'ORPHANET.EC:1', 'label': 'Verified centre', 'kind': 'expert_centre',
             'sources': ['orphanet_groups'], 'info': {'urls': {'orphanet_groups': 'https://www.orpha.net/en/expert-centres/centre/1'}, 'descriptions': {'orphanet_groups': 'Original description'}}},
            {'id': 'fake', 'label': 'Unsafe link', 'kind': 'doctor', 'info': {'urls': {'x': 'javascript:alert(1)'}}},
        ]}
        cards = graph_sources(graph, 12, '2026-10-04', contacts=True)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['kind'], 'contact')
        self.assertEqual(cards[0]['excerpt'], 'Original description')
        self.assertEqual(cards[0]['providers'], ['orphanet_groups'])
        self.assertIsNone(cards[0]['email'])

    def test_provider_verification_page_is_failure_even_with_http_200(self):
        response = SimpleNamespace(headers={'Content-Type':'text/html'}, text='<title>Vérification de la connexion...</title>')
        source = SimpleNamespace(session=SimpleNamespace(request=lambda *args, **kwargs: response))
        bound_requests(source, time.monotonic() + 10)
        with self.assertRaisesRegex(RuntimeError, 'interactive access'):
            source.session.request('GET', 'https://www.orpha.net/')

    def test_contacts_exclude_trials_reached_only_through_a_parent_condition(self):
        graph = {'focus':['MONDO:1'], 'nodes':[
            {'id':'MONDO:1','label':'Specific condition','kind':'disease','info':{}},
            {'id':'MONDO:2','label':'Broad category','kind':'disease','info':{}},
            {'id':'NCT:NCT1','label':'Other disease trial','kind':'clinical_trial','info':{}},
        ], 'edges':[
            {'from':'MONDO:1','to':'MONDO:2','relation':'subclass_of'},
            {'from':'MONDO:2','to':'NCT:NCT1','relation':'clinical_trial'},
        ]}
        self.assertEqual(graph_sources(graph, 12, '2026-10-04', contacts=True), [])


if __name__ == '__main__':
    unittest.main()
