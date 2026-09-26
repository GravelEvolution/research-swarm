import tempfile
import unittest
from pathlib import Path

from research_swarm.runner import ResearchRunner, validate_result
from research_swarm.tools import ResearchTools


LIBRARY = {
    'topic': {'title': '效率研究'},
    'papers': [{'id': '1', 'title': 'A', 'abstract': 'A proposes sparse attention.', 'score': 80, 'facetNodeIds': ['1'], 'evidenceIds': ['10'], 'pdfAvailable': False, 'codeUrl': ''},
               {'id': '2', 'title': 'B', 'abstract': 'B proposes linear attention.', 'score': 70, 'facetNodeIds': ['2'], 'evidenceIds': ['20'], 'pdfAvailable': False, 'codeUrl': ''}],
    'facetNodes': [{'id': '1', 'title': '稀疏注意力', 'facetName': '方法树', 'topology': 'Tree', 'parentId': None, 'paperIds': ['1']},
                   {'id': '2', 'title': '线性注意力', 'facetName': '方法树', 'topology': 'Tree', 'parentId': None, 'paperIds': ['2']}],
    'evidence': [{'id': '10', 'paperId': '1', 'type': 'abstract', 'quote': 'A proposes sparse attention.', 'locator': 'Abstract'},
                 {'id': '20', 'paperId': '2', 'type': 'abstract', 'quote': 'B proposes linear attention.', 'locator': 'Abstract'}],
}


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runner = ResearchRunner(None, Path(self.temp.name))
        self.context = {'library': LIBRARY, 'requirements': [{'id': 'r1', 'description': '对比效率', 'acceptance': '证据表'}], 'mode': 'evidence', 'round': 1, 'children': []}

    def tearDown(self):
        self.temp.cleanup()

    def test_unverifiable_evidence_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '证据'):
            validate_result({'summary': 'claim', 'claims': [{'text': 'fast', 'evidenceIds': ['999']}]}, LIBRARY)

    def test_empty_optional_facet_reference_means_unassigned(self):
        task = {'title': '比较', 'description': '对比机制', 'acceptance': '说明局限', 'sourceNodeId': ''}
        result = validate_result({'summary': '计划', 'children': [task]}, LIBRARY)
        self.assertNotIn('sourceNodeId', result['children'][0])

    def test_invalid_model_evidence_is_repaired_with_one_bounded_validation_retry(self):
        import json
        calls, logs = [], []
        class Provider:
            def chat(self, messages, **kwargs):
                calls.append(messages[-1]['content'])
                return json.dumps({'summary': '核验', 'claims': [{'text': '候选', 'evidenceIds': ['999' if len(calls) == 1 else '10']}]})
        result = ResearchRunner(Provider(), Path(self.temp.name))({'id': 'leaf', 'phase': 'execute', 'input': {}}, dict(self.context, mode='llm'), logs.append)
        self.assertEqual(result['claims'][0]['evidenceIds'], ['10'])
        self.assertEqual(len(calls), 2)
        self.assertIn('999', calls[1])
        self.assertTrue(any('校验' in log for log in logs))

    def test_claim_cannot_self_approve(self):
        result = validate_result({'summary': 'candidate', 'claims': [{'text': 'fast', 'status': 'confirmed', 'evidenceIds': []}]}, LIBRARY)
        self.assertEqual(result['claims'][0]['status'], 'candidate')
        self.assertIn('无证据', result['claims'][0]['limitations'])

    def test_audit_decomposes_then_grounds_leaf_without_model(self):
        root = self.runner({'id': 'central', 'phase': 'plan', 'kind': 'research', 'input': {}}, self.context, lambda _: None)
        self.assertGreaterEqual(len(root['children']), 2)
        branch = self.runner({'id': 'facet:1', 'sourceNodeId': '1', 'phase': 'plan', 'input': root['children'][0]}, self.context, lambda _: None)
        self.assertTrue(branch['children'])
        leaf = self.runner({'id': 'leaf', 'phase': 'execute', 'kind': 'evidence', 'input': {'paperIds': ['1']}}, self.context, lambda _: None)
        self.assertEqual(leaf['evidenceIds'], ['10'])
        self.assertIn('摘要', leaf['summary'])
        self.assertTrue(all(c['status'] == 'candidate' for c in leaf['claims']))

    def test_missing_experiment_data_does_not_invent_result(self):
        result = self.runner({'id': 'ex', 'phase': 'execute', 'kind': 'experiment', 'input': {}}, self.context, lambda _: None)
        self.assertTrue(result['unresolved'])
        self.assertFalse(result.get('generatedEvidence'))

    def test_numeric_experiment_generates_traceable_artifact(self):
        result = self.runner({'id': 'ex', 'phase': 'execute', 'kind': 'experiment', 'input': {'experiment': {'metric': 'latency_ms', 'groups': {'A': [1, 2, 3], 'B': [4, 5, 6]}, 'lowerIsBetter': True}}}, self.context, lambda _: None)
        ev = result['generatedEvidence'][0]
        self.assertEqual(ev['type'], 'experiment')
        self.assertTrue((Path(self.temp.name) / ev['locator']).is_file())
        self.assertEqual(result['structured']['experiment']['groups']['A']['mean'], 2)
        self.assertEqual(result['claims'][0]['status'], 'candidate')

    def test_research_tools_reject_shell_and_unknown_paper(self):
        tools = ResearchTools(LIBRARY)
        with self.assertRaises(ValueError):
            tools.call('shell', {'command': 'echo test'})
        with self.assertRaises(ValueError):
            tools.call('paper_read', {'paperId': '999'})

    def test_non_finite_numeric_experiment_rejected(self):
        with self.assertRaises(ValueError):
            self.runner({'id': 'ex', 'phase': 'execute', 'kind': 'experiment', 'input': {'experiment': {'groups': {'A': [1, float('nan')], 'B': [3, 4]}}}}, self.context, lambda _: None)

    def test_empty_selected_scope_does_not_use_unrelated_papers(self):
        import copy
        context = copy.deepcopy(self.context)
        context['library']['facetNodes'].append({'id': '3', 'title': '新范围', 'parentId': None, 'paperIds': []})
        result = self.runner({'id': 'facet:3', 'phase': 'plan', 'kind': 'research', 'sourceNodeId': '3', 'input': {}}, context, lambda _: None)
        self.assertFalse(result.get('children'))
        self.assertTrue(result['unresolved'])

    def test_aggregation_preserves_original_claim_author(self):
        context = dict(self.context)
        context['children'] = [{'id': 'leaf-a', 'title': '原始核验', 'output': {'summary': '摘要核验', 'claims': [{'id': 'claim-a', 'nodeId': 'leaf-a', 'text': 'A 有摘要材料', 'evidenceIds': ['10'], 'status': 'candidate'}], 'evidenceIds': ['10'], 'unresolved': []}}]
        result = self.runner({'id': 'central', 'phase': 'aggregate', 'kind': 'research', 'input': {}}, context, lambda _: None)
        self.assertEqual(result['claims'][0].get('nodeId'), 'leaf-a')

    def test_explicit_ranges_are_not_deduplicated_or_silently_capped(self):
        import copy
        context = copy.deepcopy(self.context)
        context['library']['facetNodes'] = [{'id': str(i), 'title': f'维度{i}', 'parentId': None, 'paperIds': ['1']} for i in range(6)]
        context['requirements'][0]['sourceNodeIds'] = [str(i) for i in range(6)]
        result = self.runner({'id': 'central', 'phase': 'plan', 'input': {}}, context, lambda _: None)
        self.assertEqual({c['sourceNodeId'] for c in result['children']}, {str(i) for i in range(6)})

    def test_empty_retrieval_does_not_fall_back_to_previous_library(self):
        result = self.runner({'id': 'deep', 'phase': 'plan', 'input': {'paperIds': [], 'paperScopeExplicit': True}}, self.context, lambda _: None)
        self.assertFalse(result.get('children'))
        self.assertTrue(result['unresolved'])

    def test_model_can_request_authorized_new_papers_and_only_real_return_is_merged(self):
        import copy
        import json
        class Provider:
            replies = [json.dumps({'toolCalls': [{'name': 'paper_retrieve', 'arguments': {'query': 'sparse attention', 'limit': 1}}]}), json.dumps({'summary': '新增论文仅有摘要', 'claims': [{'text': '新材料待核验', 'evidenceIds': ['30']}], 'evidenceIds': ['30']})]
            def chat(self, messages, **kwargs):
                return self.replies.pop(0)
        received = []
        def retrieve(node, context, query, limit):
            received.append((query, limit))
            library = copy.deepcopy(LIBRARY)
            library['papers'].append({'id': '3', 'title': 'New result', 'evidenceIds': ['30']})
            library['evidence'].append({'id': '30', 'paperId': '3', 'quote': 'New abstract', 'type': 'abstract', 'locator': 'Abstract'})
            return {'library': library, 'retrieval': {'resultPaperIds': ['3']}}
        runner = ResearchRunner(Provider(), Path(self.temp.name), retrieve=retrieve)
        context = dict(self.context, mode='llm')
        result = runner({'id': 'deep', 'phase': 'execute', 'input': {'allowNewSearch': True, 'searchBudgetId': 'approved'}}, context, lambda _: None)
        self.assertEqual(received, [('sparse attention', 1)])
        self.assertEqual(len(result['sourceLibrary']['papers']), 3)
        self.assertEqual(result['claims'][0]['nodeId'], 'deep')

    def test_model_external_search_cannot_bypass_node_authorization(self):
        class Provider:
            def chat(self, messages, **kwargs):
                return '{"toolCalls":[{"name":"paper_retrieve","arguments":{"query":"anything"}}]}'
        runner = ResearchRunner(Provider(), Path(self.temp.name), retrieve=lambda *args: self.fail('unauthorized retrieval'))
        with self.assertRaisesRegex(ValueError, '授权'):
            runner({'id': 'deep', 'phase': 'execute', 'input': {}}, dict(self.context, mode='llm'), lambda _: None)

    def test_model_aggregation_preserves_unique_exact_origin_without_returned_id(self):
        import json
        original = {'id': 'claim-a', 'nodeId': 'leaf-a', 'text': 'A 有摘要材料', 'evidenceIds': ['10']}
        class Provider:
            def chat(self, messages, **kwargs):
                return json.dumps({'summary': '汇总', 'claims': [{'text': original['text'], 'evidenceIds': ['10'], 'nodeId': 'forged'}]})
        context = dict(self.context, mode='llm', children=[{'id': 'leaf-a', 'output': {'claims': [original]}}])
        result = ResearchRunner(Provider(), Path(self.temp.name))({'id': 'central', 'phase': 'aggregate', 'input': {}}, context, lambda _: None)
        self.assertEqual(result['claims'][0]['nodeId'], 'leaf-a')
        self.assertEqual(result['claims'][0]['id'], 'claim-a')

    def test_model_changed_evidence_cannot_reuse_child_author(self):
        import json
        original = {'id': 'claim-a', 'nodeId': 'leaf-a', 'text': '存在摘要材料', 'evidenceIds': ['10']}
        class Provider:
            def chat(self, messages, **kwargs):
                return json.dumps({'summary': '汇总', 'claims': [dict(original, evidenceIds=['20'])]})
        context = dict(self.context, mode='llm', children=[{'id': 'leaf-a', 'output': {'claims': [original]}}])
        result = ResearchRunner(Provider(), Path(self.temp.name))({'id': 'central', 'phase': 'aggregate', 'input': {}}, context, lambda _: None)
        self.assertEqual(result['claims'][0]['nodeId'], 'central')

    def test_autonomous_root_can_propose_validated_followups(self):
        import json
        followup = {'title': '核验数据条件', 'description': '检查双方数据集', 'acceptance': '可比性说明与证据', 'kind': 'evidence', 'paperIds': ['1'], 'requirementIds': ['r1']}
        class Provider:
            def chat(self, messages, **kwargs):
                return json.dumps({'summary': '资料仍有缺口', 'followups': [followup], 'claims': []})
        context = dict(self.context, mode='llm', workflow='autonomous', iteration=1, maxIterations=3)
        runner = ResearchRunner(Provider(), Path(self.temp.name))
        result = runner({'id': 'central', 'phase': 'aggregate', 'input': {}}, context, lambda _: None)
        self.assertEqual(result['followups'][0]['title'], followup['title'])
        result = runner({'id': 'leaf', 'phase': 'execute', 'input': {}}, context, lambda _: None)
        self.assertNotIn('followups', result)

    def test_model_leaf_reads_real_page_callback_and_can_cite_new_locator(self):
        import copy
        import json
        context = copy.deepcopy(self.context)
        context['mode'] = 'llm'
        context['library']['papers'][0]['pdfAvailable'] = True
        evidence = {'id': 'pdf:1:abcdef:p1', 'paperId': '1', 'quote': 'Actual PDF page text', 'locator': 'PDF 第 1 页', 'type': 'full_text', 'extractor': 'pypdf'}
        calls = []
        class Provider:
            def chat(self, messages, **kwargs):
                inputs = json.loads(messages[1]['content'])
                assert evidence in inputs['library']['evidence']
                return json.dumps({'summary': '已核验第一页', 'claims': [{'text': '材料定位', 'evidenceIds': [evidence['id']]}]})
        def read_pdf(paper_id, page_start, page_count):
            calls.append((paper_id, page_start, page_count))
            return {'totalPages': 12, 'evidence': [evidence]}
        runner = ResearchRunner(Provider(), Path(self.temp.name), read_pdf=read_pdf)
        result = runner({'id': 'leaf', 'kind': 'evidence', 'phase': 'execute', 'input': {'paperIds': ['1']}}, context, lambda _: None)
        self.assertEqual(calls, [('1', 1, 3)])
        self.assertIn(evidence, result['sourceLibrary']['evidence'])
        self.assertNotIn(evidence, context['library']['evidence'])


if __name__ == '__main__':
    unittest.main()
