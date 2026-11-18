"""Small, auditable resource-producer retrieval and uncertainty policy."""
from pathlib import Path
import re

IDENTIFIER = re.compile(r'\b[A-Za-z_$][A-Za-z0-9_$]*\b')
FUNCTION = re.compile(r'\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\([^)]*\)[^{]*\{')
WRITE_SINKS = ('writeFileSync', 'appendFileSync', 'renameSync')
SOURCE_SUFFIXES = ('.ts', '.mts', '.tsx', '.js', '.mjs')


def added_identifiers(diff):
    text = '\n'.join(line[1:] for line in diff.splitlines()
                     if line.startswith('+') and not line.startswith('+++'))
    ignored = {'const', 'let', 'var', 'if', 'return', 'function', 'true', 'false',
               'new', 'Set', 'string', 'undefined', 'path', 'fs'}
    return sorted(set(IDENTIFIER.findall(text)) - ignored,
                  key=lambda name: (not name.endswith('Path'), name))


def _functions(source):
    lines = source.splitlines()
    results = []
    for match in FUNCTION.finditer(source):
        depth = 1
        position = match.end()
        while position < len(source) and depth:
            depth += (source[position] == '{') - (source[position] == '}')
            position += 1
        if depth:
            continue
        start = source.count('\n', 0, match.start()) + 1
        end = source.count('\n', 0, position) + 1
        results.append({'symbol': match.group(1), 'start_line': start, 'end_line': end,
                        'content': '\n'.join(lines[start - 1:end])})
    return results


def find_resource_producers(snapshot, diff):
    snapshot = Path(snapshot)
    anchors = [name for name in added_identifiers(diff) if name.endswith('Path')]
    if not anchors:
        return []
    selected = []
    for path in sorted(p for p in snapshot.rglob('*') if p.suffix in SOURCE_SUFFIXES and p.is_file()):
        source = path.read_text()
        functions = _functions(source)
        for function in functions:
            hits = [anchor for anchor in anchors if re.search(
                rf'\b(?:{"|".join(WRITE_SINKS)})\s*\([^\n;]*\b{re.escape(anchor)}\b',
                function['content'])]
            if not hits:
                continue
            selected.append({'file': str(path.relative_to(snapshot)), 'kind': 'automatic_producer_source',
                             'anchors': hits, **function})
            local_names = {item['symbol']: item for item in functions}
            for name, dependency in local_names.items():
                if name != function['symbol'] and re.search(rf'\b{re.escape(name)}\s*\(', function['content']):
                    selected.append({'file': str(path.relative_to(snapshot)),
                        'kind': 'automatic_producer_dependency', 'anchors': hits, **dependency})
    unique = {}
    for item in selected:
        unique[item['file'], item['start_line'], item['symbol']] = item
    return [unique[key] for key in sorted(unique)]


def needs_runtime_probe(diff, producer_blocks):
    combined = diff + '\n' + '\n'.join(block['content'] for block in producer_blocks)
    has_contract = ('.has(' in diff and any(sink in combined for sink in WRITE_SINKS))
    transformations = ('.replace(', '.substring(', '.toUpperCase(', '.toLowerCase(',
                       'basename(', 'normalize')
    reasons = []
    if has_contract and any(operation in combined for operation in transformations):
        reasons.append('cross_boundary_string_normalization')
    return {'needed': bool(reasons), 'reasons': reasons}
