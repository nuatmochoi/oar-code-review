"""Verify the frozen inputs and replay the historical request construction offline."""
import json
import shutil

from experiment import ROOT, PORT, load, sha, write
from merge import PRIOR, BASE, merge_input, merge_request, packets, select


def main():
    prior = load(PRIOR/'freeze.json')
    add = load(PRIOR/'add_freeze.json')
    for file, expected in (prior['hashes'] | add['hashes']).items():
        assert sha(file) == expected, file
    assert not (ROOT/'freeze.json').exists()
    assert not (ROOT/'api').exists()
    upstream = ROOT/'upstream'
    upstream.mkdir()
    for name in ('package.json','package-lock.json','scripts','schema','corpus','golden','node_modules'):
        (upstream/name).symlink_to((PRIOR/'upstream'/name).resolve())
    assert (ROOT/'judge-repos').is_dir() and not (ROOT/'judge-repos').is_symlink()
    shutil.copytree(PRIOR/'judge-config',ROOT/'judge-config')
    config = ROOT/'judge-config/models.json'
    models = load(config)
    for provider in models['providers'].values():
        provider['baseUrl'] = provider['baseUrl'].replace(':8796/', f':{PORT}/')
    config.write_text(json.dumps(models,indent=2))

    inputs, checks, prior_files = [], [], []
    for packet in packets():
        index = packet['index']
        base, cap, eligible = merge_input(packet,'DIVERGENT')
        old = PRIOR/'arms/ADD/review'/f'{index:02d}'
        previous = load(old/'complete.json')
        if eligible and cap:
            rebuilt = merge_request(packet,base['findings'],eligible)
            assert rebuilt == load(old/'verify/request.json'), index
            chosen, withheld, _ = select(load(old/'verify/parsed.json'),eligible,cap)
            assert chosen == previous['added'] and withheld == previous['withheld_by_cap'], index
        else:
            assert not (old/'verify/request.json').exists() and previous['usage'] is None
        general, general_cap, new_eligible = merge_input(packet,'GENERAL')
        assert general == base and general_cap == cap
        inputs.extend([PRIOR/'public'/f'{index:02d}.json',BASE/f'{packet["key"]}.json'])
        for source_arm in ('GENERAL','DIVERGENT'):
            source = PRIOR/'arms'/source_arm/'review'/f'{index:02d}'
            inputs.extend(source/name for name in ('candidate_ledger.json','complete.json'))
        prior_files.extend(f for f in old.rglob('*.json') if f.is_file())
        checks.append(dict(index=index,key=packet['key'],report_slots=cap,
                           historical_request_matches=True, historical_selection_matches=True,
                           supported_general=len(new_eligible),supported_divergent=len(eligible),
                           general_verification=bool(cap and new_eligible),divergent_verification=bool(cap and eligible)))
    write(ROOT/'preflight.json',dict(paired_checks=checks,PRs=25,used_gold_in_merge=False))
    prior_cost = load(PRIOR/'completion.json')['physical_api']['conservative_usd']
    prior_files.extend(PRIOR/name for name in ('completion.json','summary.json','RESULTS_KO.md','additive.py',
                                             'add_ranking_recovery.json','add_freeze.json','freeze.json'))
    prior_files.extend((PRIOR/'arms/ADD/findings').glob('*.json'))
    prior_files.extend((PRIOR/'arms/ADD/scoring').glob('*'))
    files = [*ROOT.glob('*.py'),ROOT/'PROTOCOL_KO.md',ROOT/'preflight.json',config,
             ROOT/'judge-config/bin/fd',*inputs,*prior_files]
    hashes = prior['hashes'] | add['hashes'] | {str(f):sha(f) for f in files if f.is_file()}
    write(ROOT/'freeze.json',dict(PRs=25, prior_api_usd=prior_cost, cumulative_cap_usd=70,
                                 new_api_cap_usd=70-prior_cost, hashes=hashes))
    print('25 replayed selections,',sum(c['divergent_verification'] for c in checks),'identical historical requests,',
          sum(c['general_verification'] for c in checks),'new verifications,',len(hashes),'frozen files')


if __name__ == '__main__':
    main()
