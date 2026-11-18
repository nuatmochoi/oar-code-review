"""Keep complete schema-valid items when the common reviewer output cap is reached."""
import json

import jsonschema

from experiment import load, sha, write


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


def recover(request, folder, response):
    raw = folder/'response.json'
    assert load(folder/'request.json')==request
    text = ''.join(block['text'] for message in response['output'] if message['type']=='message'
                   for block in message['content'] if block['type']=='output_text')
    schema = request['text']['format']['schema']
    parsed, closed = complete_prefix(text,tuple(schema['properties']))
    jsonschema.validate(parsed,schema)
    path = folder/'recovered_prefix.json'
    record = dict(raw_sha256=sha(raw),closed_arrays=closed,parsed=parsed)
    if path.exists():
        assert load(path)==record
    else:
        write(path,record)
    return parsed
