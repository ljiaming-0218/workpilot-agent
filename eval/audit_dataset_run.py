"""Summarize actual execution evidence without treating model verdicts as truth."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean


def audit(directory):
    rows = [json.loads(line) for line in (directory / 'raw.jsonl').read_text(encoding='utf-8').splitlines()]
    grades = [json.loads(line) for line in (directory / 'judgments.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    pairs, budgets, variants, features = [], [], {}, []
    for case_id in dict.fromkeys(row['case_id'] for row in rows):
        pair = [row for row in rows if row['case_id'] == case_id]
        responses = [{json.dumps({'tool': call['tool'], 'inputs': call['inputs']}, sort_keys=True): call['output_hash']
            for call in row['tool_calls'] if 'output_hash' in call} for row in pair]
        common = set(responses[0]).intersection(*responses[1:])
        pairs.append({'case_id': case_id, 'arms': len(pair),
            'same_fixture': len({row['fixture_hash'] for row in pair}) == 1,
            'common_requests': len(common),
            'same_common_responses': all(len({response[key] for response in responses}) == 1 for key in common)})
    for row in rows:
        usage = row['usage']
        calls = [event for event in row['events'] if event['event_type'] == 'llm_call']
        output_tokens = [(event.get('tokens_json') or {}).get('output', 0) for event in calls]
        budgets.append({'case_id': row['case_id'], 'variant': row['variant'],
            'passed': len(row['tool_calls']) <= 9 and usage['calls'] <= usage['call_limit']
            and usage['reserved_token_allowance'] <= usage['token_limit']
            and usage['input_chars'] <= usage['input_char_limit'] and max(output_tokens, default=0) <= 2048})
        wide = [call for call in row['tool_calls'] if call['tool'] == 'query_error_logs'
                and call['inputs'].get('minutes', 0) > 60]
        errors = [call for call in row['tool_calls'] if call.get('error')]
        has_other_evidence = any(item['service'] != row['result']['agent_tasks'][0]['service']
            and item['output'].get('items') for item in row['result'].get('evidence', [])) if errors else None
        features.append({'case_id': row['case_id'], 'variant': row['variant'],
            'injected_faults': [call['error'] for call in errors],
            'other_service_evidence_retained': has_other_evidence,
            'wider_window_queries': len(wide),
            'wider_window_records': sum(len(item['output'].get('items', []))
                for item in row['result'].get('evidence', [])
                if item['tool'] == 'query_error_logs' and item['inputs'].get('minutes', 0) > 60),
            'model_errors': dict(Counter(event['error'] for event in calls if event.get('error')))})
    for variant in dict.fromkeys(row['variant'] for row in rows):
        group = [row for row in rows if row['variant'] == variant]
        assessments = [assessment for grade in grades if grade['status'] == 'graded'
            for assessment in grade['assessments'] if assessment['variant'] == variant]
        variants[variant] = {'runs': len(group), 'execution_completed': sum(row['result']['status'] == 'completed' for row in group),
            'verifier_completed': sum(row['result'].get('verifier', {}).get('status') == 'completed' for row in group),
            'mean_latency_seconds': round(mean(row['latency_ms'] for row in group) / 1000, 3),
            'model_calls': sum(row['usage']['calls'] for row in group),
            'observed_tokens': sum((event.get('tokens_json') or {}).get('total', 0)
                for row in group for event in row['events'] if event['event_type'] == 'llm_call'),
            'token_usage_complete': all(row['usage']['tokens'] is not None for row in group),
            'tool_attempts': sum(len(row['tool_calls']) for row in group),
            'model_judge_pass': sum(item['rubric_checked_model_pass'] for item in assessments),
            'model_judged': len(assessments), 'ungraded': len(group) - len(assessments)}
    return {'raw_sha256': sha256((directory / 'raw.jsonl').read_bytes()).hexdigest(),
        'dataset_hash_matches_manifest': sha256((directory / 'dataset.json').read_bytes()).hexdigest() == manifest['dataset_sha256'],
        'variants': variants, 'paired_fixture_checks': pairs, 'budget_checks': budgets, 'feature_observations': features,
        'judge_failed_cases': [grade['case_id'] for grade in grades if grade['status'] != 'graded'],
        'judge_tokens': sum((event.get('tokens') or {}).get('total', 0) for grade in grades for event in grade['usage']),
        'limits': ['Synthetic fixture tools and online model; one repetition.',
            'Model judge verdicts are not human accuracy; schema failures and grading disagreements are retained.',
            'Code changes after this batch require a separate result directory.', 'USD cost unknown; no price assumption.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    output = args.results / 'audit_summary.json'
    if output.exists():
        raise FileExistsError(output)
    result = audit(args.results)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'variants': result['variants'], 'budgets_pass': all(row['passed'] for row in result['budget_checks']),
        'fixtures_pass': all(row['same_fixture'] and row['same_common_responses'] for row in result['paired_fixture_checks']),
        'judge_failed_cases': result['judge_failed_cases']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
