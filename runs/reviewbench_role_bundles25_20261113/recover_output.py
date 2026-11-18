"""Recover complete JSON array items from a provider output stopped at its token cap."""
import json

import jsonschema

import adaptive
from experiment import load, sha, write
from run import main


DECODER = json.JSONDecoder()
def complete_prefix(raw, fields):
    """Read only complete items of emitted top-level arrays, in their emitted order."""
    result = {field:[] for field in fields}
    started, closed = [], []
    remaining = raw.lstrip()
    if not remaining.startswith('{'):
        raise ValueError('Expected a structured review object')
    remaining = remaining[1:].lstrip()
    while remaining and not remaining.startswith('}'):
        try:
            field, end = DECODER.raw_decode(remaining)
        except json.JSONDecodeError:
            break
        if field not in fields or field in started:
            raise ValueError('Unexpected field in structured review')
        remaining = remaining[end:].lstrip()
        if not remaining.startswith(':'):
            break
        remaining = remaining[1:].lstrip()
        if not remaining.startswith('['):
            break
        started.append(field)
        remaining = remaining[1:].lstrip()
        while not remaining.startswith(']'):
            try:
                item, end = DECODER.raw_decode(remaining)
            except json.JSONDecodeError:
                return result, closed
            result[field].append(item)
            remaining = remaining[end:].lstrip()
            if not remaining.startswith(','):
                break
            remaining = remaining[1:].lstrip()
        if not remaining.startswith(']'):
            break
        closed.append(field)
        remaining = remaining[1:].lstrip()
        if not remaining.startswith(','):
            break
        remaining = remaining[1:].lstrip()
    return result, closed


def recovery_call(original, request, folder):
    raw = folder/'response.json'
    if folder.name not in ('plan','update','verify') or not raw.exists():
        return original(request,folder)
    response = load(raw)
    if response.get('incomplete_details',{}) != {'reason':'max_output_tokens'} or response.get('status')!='incomplete':
        return original(request,folder)
    assert load(folder/'request.json')==request
    text = ''.join(block['text'] for message in response['output'] if message['type']=='message'
                   for block in message['content'] if block['type']=='output_text')
    schema = request['text']['format']['schema']
    parsed, closed = complete_prefix(text,tuple(schema['properties']))
    jsonschema.validate(parsed,schema)
    recovered = folder/'recovered_prefix.json'
    record = dict(raw_sha256=sha(raw),closed_arrays=closed,
                  complete_items={field:len(items) for field,items in parsed.items()},
                  model_calls_added=0,parsed=parsed)
    if recovered.exists():
        assert load(recovered)==record
    else:
        write(recovered,record)
    print(f'Recovered complete output prefix for {folder.parent.name}: {record["complete_items"]}',flush=True)
    return parsed,response['usage']


if __name__=='__main__':
    original = adaptive.api_call
    adaptive.api_call = lambda request, folder: recovery_call(original,request,folder)
    main()
