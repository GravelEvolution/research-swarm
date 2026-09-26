"""Research-only tool registry. No shell, browser agent, or unrestricted file API."""
from __future__ import annotations

import math
import random
import statistics


def experiment_statistics(descriptor: dict) -> dict:
    groups = descriptor.get('groups', {})
    if not isinstance(groups, dict) or not 2 <= len(groups) <= 8:
        raise ValueError('实验数据需要 2 至 8 组已观测数值')
    normalized = {}
    for name, values in groups.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(values, list) or not 2 <= len(values) <= 10000:
            raise ValueError('每组须有名称及 2 至 10000 个观测值')
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or abs(v) > 1e100 for v in values):
            raise ValueError('实验输入仅接受有限数值')
        normalized[name] = [float(v) for v in values]
    summaries = {name: {'n': len(v), 'mean': statistics.fmean(v), 'median': statistics.median(v), 'stdev': statistics.stdev(v), 'min': min(v), 'max': max(v)} for name, v in normalized.items()}
    names = list(normalized)
    baseline = names[0]
    randomizer = random.Random(2026)
    comparisons = []
    for name in names[1:]:
        a, b = normalized[baseline], normalized[name]
        # Bounded 500-resample percentile interval for independent observations.
        # Resampling is capped in work, not truncated in the reported sample means.
        repetitions = min(500, max(100, 1000000 // (len(a) + len(b))))
        diffs = sorted(statistics.fmean(randomizer.choices(b, k=len(b))) - statistics.fmean(randomizer.choices(a, k=len(a))) for _ in range(repetitions))
        comparisons.append({'baseline': baseline, 'alternative': name, 'difference': summaries[name]['mean'] - summaries[baseline]['mean'], 'interval95': [diffs[int(repetitions * .025)], diffs[min(repetitions - 1, int(repetitions * .975))]], 'bootstrapReplicates': repetitions})
    return {'metric': str(descriptor.get('metric', 'value'))[:100], 'lowerIsBetter': bool(descriptor.get('lowerIsBetter', True)), 'groups': summaries, 'comparisons': comparisons, 'method': '独立样本均值差；固定随机种子 2026 的 percentile bootstrap 95% 区间', 'limitations': '结果仅描述用户提供的观测值。采样独立性、同硬件/数据集/预算、配对关系及重复实验充分性需人工核验；不能据此自动宣布方案优越。'}


class ResearchTools:
    names = ('paper_search', 'paper_read', 'evidence_lookup', 'facet_read')

    def __init__(self, library: dict, read_pdf=None):
        self.library = library
        self.read_pdf = read_pdf

    def call(self, name: str, arguments: dict) -> dict:
        if name not in self.names:
            raise ValueError(f'不允许调用工具：{name}')
        if not isinstance(arguments, dict):
            raise ValueError('工具参数必须是对象')
        if name == 'paper_search':
            query = str(arguments.get('query', '')).strip().lower()
            terms = query.split()
            papers = self.library.get('papers', [])
            ranked = sorted(papers, key=lambda p: sum(t in (p['title'] + ' ' + p.get('abstract', '')).lower() for t in terms), reverse=True)
            return {'papers': [{k: p.get(k) for k in ('id', 'title', 'abstract', 'evidenceIds', 'score', 'facetNodeIds')} for p in ranked[:min(15, max(1, int(arguments.get('limit', 8))))]], 'scope': '当前真实论文库；外部补充检索由用户在节点深入研究中启动'}
        if name == 'paper_read':
            paper = next((p for p in self.library.get('papers', []) if p['id'] == str(arguments.get('paperId'))), None)
            if paper is None:
                raise ValueError('论文 ID 不存在')
            result = {'paper': paper, 'evidence': [e for e in self.library.get('evidence', []) if e['paperId'] == paper['id']]}
            if self.read_pdf:
                result['fullText'] = self.read_pdf(paper['id'], arguments.get('pageStart', 1), arguments.get('pageCount', 3))
            return result
        if name == 'evidence_lookup':
            ids = {str(x) for x in arguments.get('evidenceIds', [])}
            evs = [e for e in self.library.get('evidence', []) if e['id'] in ids]
            if len(evs) != len(ids):
                raise ValueError('证据 ID 不存在')
            return {'evidence': evs}
        facet = next((n for n in self.library.get('facetNodes', []) if n['id'] == str(arguments.get('nodeId'))), None)
        if facet is None:
            raise ValueError('切面节点不存在')
        return {'node': facet, 'children': [n for n in self.library.get('facetNodes', []) if n.get('parentId') == facet['id']]}
