import json
from collections import Counter

import pytest

from backend.mcp.client import MCPTransientConnectionError
from eval.model_comparison import ROOT, Fixture
from eval.run_dataset_experiment import blind_answer, grade_pair, PairGrade
from eval.model_comparison import run_variant
from tests.test_model_comparison import Model
from backend.schemas.agent import InvestigatorDecision


def cases():
    return json.loads((ROOT / 'eval/datasets/w4_v1.json').read_text(encoding='utf-8'))['cases']


def test_frozen_dataset_and_tool_gold_isolation():
    rows = cases()
    assert len(rows) == len({r['id'] for r in rows}) == 18
    assert Counter(r['split'] for r in rows) == {'development': 6, 'holdout': 12}
    for case in rows:
        assert case['rubric']['must_include'] and case['rubric']['must_not']
        fixture = Fixture(case)
        assert not hasattr(fixture, 'truth') and not hasattr(fixture, 'rubric')


def test_retry_and_wider_window_have_actual_distinct_outputs():
    rows = {r['id']: r for r in cases()}
    fixture = Fixture(rows['transient_read_retry'])
    args = {'service_name': fixture.services[0], 'minutes': 60}
    with pytest.raises(MCPTransientConnectionError):
        fixture.call_tool('query_error_logs', args)
    assert fixture.call_tool('query_error_logs', args)['total'] == 1
    fixture = Fixture(rows['followup_finds_logs'])
    args['service_name'] = fixture.services[0]
    assert fixture.call_tool('query_error_logs', args)['total'] == 0
    assert fixture.call_tool('query_error_logs', {**args, 'minutes': 240})['total'] == 1


def test_knowledge_failure_uses_knowledge_query_scope():
    case = next(row for row in cases() if row['id'] == 'knowledge_tool_failure')
    fixture = Fixture(case)
    with pytest.raises(RuntimeError, match='controlled fixture failure'):
        fixture.call_tool('search_knowledge', {'query': fixture.services[0] + ' troubleshooting'})
    assert fixture.call_tool('search_knowledge', {'query': fixture.services[1] + ' troubleshooting'})['total'] == 0


def test_blinding_and_fixed_check_cannot_be_overruled_by_judge():
    class Judge:
        def generate_structured(self, system, user, schema):
            assert schema is PairGrade
            assert 'enhanced_single_equal' not in user
            assert 'verifier_verdict' not in user
            return PairGrade(assessments=[dict(label=label, cause='pass', action='pass',
                uncertainty='not_applicable', citations='pass', completeness='pass', reason='ok')
                for label in ('X', 'Y')])
    rows = [dict(variant=variant, tool_calls=[], result=dict(status='completed',
        final_answer='heading\narchitecture\nanswer', evidence=[{'agent_id': variant, 'output': 'record'}],
        verifier_verdict='accepted'), scoring_v2=dict(has_displayed_recommendation=True,
        review_flags=[{'issue': 'quantitative_bound_not_found_in_cited_records'}]))
        for variant in ('enhanced_single_equal', 'multi')]
    assert 'agent_id' not in blind_answer(rows[0])['retrieved_evidence'][0]
    grade = grade_pair(cases()[0], rows, Judge())
    assert grade['status'] == 'graded'
    assert all(not item['rubric_checked_model_pass'] for item in grade['assessments'])


def test_unknown_cause_legacy_scoring_does_not_discard_valid_single_answer():
    case = next(row for row in cases() if row['id'] == 'historical_ticket')
    record = run_variant(case, 'enhanced_single_equal', Model(), 200)
    assert record['result']['status'] == 'completed'
    assert record['result']['evidence'] and record['result']['final_answer']
    assert record['scores']['uncertainty_in_summary'] in (True, False)


class GreedyModel(Model):
    def generate_structured(self, system, user, schema):
        decision = super().generate_structured(system, user, schema)
        if schema is InvestigatorDecision:
            payload = json.loads(user)
            allowed = [name for name in payload['allowed_next_tools'] if name != 'stop']
            decision.next_tool = allowed[0] if payload['remaining_tool_attempts'] > 0 and allowed else 'stop'
            decision.gaps = ['old missing ticket'] if len(payload['observations']) == 1 else ['current configuration missing']
        return decision


@pytest.mark.parametrize('variant', ['enhanced_single_equal', 'multi'])
@pytest.mark.parametrize('case_id,found', [('followup_finds_logs', True), ('followup_stays_empty', False)])
def test_greedy_investigator_leaves_one_attempt_for_actual_wider_window(variant, case_id, found):
    case = next(row for row in cases() if row['id'] == case_id)
    record = run_variant(case, variant, GreedyModel(), 210)
    calls = record['tool_calls']
    assert [(c['tool'], c['inputs'].get('minutes')) for c in calls] == [
        ('query_error_logs', 60), ('search_tickets', None), ('query_error_logs', 1440)]
    assert record['usage']['calls'] <= record['usage']['call_limit'] == 4
    assert record['result']['tool_budget']['used'] == 3
    assert bool(any(item['output']['items'] for item in record['result']['evidence'])) == found
    assert 'old missing ticket' not in record['result']['agent_tasks'][0]['gaps']
    assert 'old missing ticket' not in record['result']['final_answer']
