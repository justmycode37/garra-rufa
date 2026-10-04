import tempfile
import unittest
from pathlib import Path

from garra.research.atlas import AtlasIndex
from garra.research.worker import atlas_graph
from garra.ui.service import InputError
from sources.monarch import MonarchSource
from sources.base import Node
from unittest.mock import patch


class AtlasTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        directory = Path(self.temp.name)
        directory.joinpath('hp.obo').write_text('''[Term]
id: HP:0000001
name: Region

[Term]
id: HP:0000002
name: Regional phenotype
is_a: HP:0000001 ! Region

[Term]
id: HP:0000003
name: Specific phenotype
is_a: HP:0000002 ! Regional phenotype

[Term]
id: HP:0000004
name: Other region
''')
        directory.joinpath('phenotype.hpoa').write_text('''#version: 2026-09-02
database_id\tdisease_name\tqualifier\thpo_id\treference\taspect
OMIM:1\tAlpha disease\t\tHP:0000003\tPMID:123;OMIM:1\tP
OMIM:1\tAlpha disease\t\tHP:0000003\tPMID:456\tP
OMIM:2\tBeta disease\t\tHP:0000002\tPMID:123\tP
OMIM:3\tExcluded disease\tNOT\tHP:0000002\tPMID:999\tP
OMIM:4\tUnrelated disease\t\tHP:0000004\tPMID:999\tP
''')
        self.index = AtlasIndex(directory)

    def test_descendants_positive_annotations_and_citations(self):
        graph = self.index.search({'region': 'HP:0000001'})
        self.assertEqual(graph['total'], 2)
        ids = {node['id'] for node in graph['nodes']}
        self.assertTrue({'OMIM:1', 'OMIM:2', 'PMID:123', 'PMID:456'} <= ids)
        self.assertFalse({'OMIM:3', 'OMIM:4', 'PMID:999'} & ids)
        annotation = next(e for e in graph['edges'] if e['from'] == 'OMIM:1' and e['to'] == 'HP:0000003')
        self.assertEqual(annotation['evidence'], ['OMIM:1', 'PMID:123', 'PMID:456'])
        self.assertTrue(all(e['from'] in ids and e['to'] in ids for e in graph['edges']))
        self.assertEqual(graph['datasetVersion'], '2026-09-02')

    def test_stable_pagination_and_filter_search_entire_region(self):
        first = self.index.search({'region': 'HP:0000001', 'limit': 1})
        second = self.index.search({'region': 'HP:0000001', 'limit': 1, 'offset': first['nextOffset']})
        self.assertEqual(first['total'], 2)
        self.assertIsNone(second['nextOffset'])
        self.assertIn('OMIM:2', {n['id'] for n in second['nodes']})
        result = self.index.search({'region': 'HP:0000001', 'query': 'Beta'})
        self.assertEqual(result['total'], 1)
        self.assertNotIn('OMIM:1', {n['id'] for n in result['nodes']})
        self.assertEqual(self.index.search({'region': 'HP:0000001', 'query': 'absent'})['status'], 'empty')

    def test_invalid_inputs_do_not_select_files_or_unbounded_pages(self):
        for body in [{'region': '../hp.obo'}, {'region': 'HP:0000001', 'limit': True}, {'region': 'HP:0000001', 'offset': -1}, {'region': 'HP:0000001', 'limit': 10000}, {'region': 'HP:9999999'}]:
            with self.assertRaises(InputError):
                self.index.search(body)

    def test_repository_graph_preserves_provider_edges_and_contact_identity(self):
        graph = atlas_graph({'focus': ['OMIM:1'], 'nodes': [
            {'id': 'OMIM:1', 'label': 'Alpha', 'kind': 'disease', 'sources': ['hpo'], 'xrefs': ['MONDO:1']},
            {'id': 'centre:1', 'label': 'Expert centre', 'kind': 'expert_centre', 'sources': ['orphanet'], 'info': {'urls': {'orphanet': 'https://example.org/centre'}}},
            {'id': 'term:alpha', 'label': 'Search', 'kind': 'unknown'},
        ], 'edges': [{'from': 'OMIM:1', 'to': 'centre:1', 'relation': 'expert_centre', 'source': 'orphanet', 'evidence': ['PMID:123']}, {'from': 'term:alpha', 'to': 'OMIM:1', 'relation': 'matches'}]})
        self.assertEqual(len(graph['edges']), 1)
        self.assertEqual(graph['nodes'][1]['kind'], 'expert_centre')
        self.assertEqual(graph['nodes'][0]['xrefs'], ['MONDO:1'])
        self.assertIsNone(graph['nodes'][1]['email'])

    def test_monarch_retains_verified_input_alias_and_resolved_label(self):
        source = MonarchSource()
        def get(url, **kwargs):
            if '/entity/' in url:
                return {'name': 'Marfan syndrome', 'node_hierarchy': {'super_classes': [{'id': 'MONDO:2', 'name': 'Parent'}]}}
            return {'items': []}
        with patch.object(source, 'get_json', side_effect=get):
            edges = source._relations(Node('OMIM:154700', 'OMIM:154700'), 'MONDO:0007947', 12)
        self.assertEqual(edges[0].src.label, 'Marfan syndrome')
        self.assertIn('OMIM:154700', edges[0].src.xrefs)


if __name__ == '__main__':
    unittest.main()
