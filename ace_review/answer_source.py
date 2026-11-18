"""Bounded argument-evidence retrieval, not sound flow or runtime resolution.

Full source is parsed but only existing candidate chunks may be emitted.
SCIP call-token identities are used; same-name matching is never a fallback.
"""
import ast
import re
from collections import defaultdict

from .auto_context import retrieve
from .delta_scip import changed_ranges
from .question_context import canonical_blocks, select
from .obligation import render_blocks, token_count


def body_nodes(function):
    return [node for statement in function.body for node in ast.walk(statement)]


def observe_parameter(function, parameter):
    nodes = body_nodes(function)
    nested = any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)) for n in nodes)
    control = any(isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
                                ast.AsyncWith, ast.Match, ast.IfExp, ast.comprehension)) for n in nodes)
    # Potential aliases support candidate discovery only. Rebindings invalidate
    # certainty; they do not establish that a value really survives the write.
    potential, live = {parameter}, {parameter}
    transformed, used, forwarded = False, False, False
    links = []
    for statement in function.body:
        for n in ast.walk(statement):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in live:
                used = True
            if isinstance(n, ast.Call):
                arguments = [*n.args, *(kw.value for kw in n.keywords)]
                names = [a.id for a in arguments if isinstance(a, ast.Name) and a.id in potential]
                if names:
                    links.append({'line': n.lineno, 'column': n.col_offset, 'actuals': sorted(set(names)),
                                  'uncertain': any(name not in live for name in names)})
                if any(isinstance(a, ast.Name) and a.id in live for a in arguments):
                    forwarded = True
                if any(not isinstance(a, ast.Name) and any(isinstance(v, ast.Name) and v.id in live
                           for v in ast.walk(a)) for a in arguments):
                    transformed = True
                if isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id in live:
                    transformed = True  # Any method use can consume/mutate; no method-name allowlist.
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            value = statement.value
            for target in targets:
                if isinstance(target, ast.Name):
                    if target.id == parameter and not (isinstance(value, ast.Name) and value.id == parameter):
                        transformed = True
                    if isinstance(value, ast.Name) and value.id in potential:
                        potential.add(target.id)
                    if isinstance(value, ast.Name) and value.id in live:
                        live.add(target.id)
                    else:
                        live.discard(target.id)
                elif any(isinstance(v, ast.Name) and v.id in live for v in ast.walk(target)):
                    transformed = True
    status = ('unknown_nested_scope' if nested else 'unknown_control_flow' if control else
              'rebound_or_transformed' if transformed else 'forwarded_syntax' if forwarded else
              'used_not_forwarded' if used else 'unused_syntax')
    return {'parameter': parameter, 'status': status, 'potential_call_links': links,
            'runtime_verified': False}


def bind_arguments(call, function):
    """Only unambiguous explicit bindings; **mapping and post-*args stay unknown."""
    positional = list(function.args.posonlyargs) + list(function.args.args)
    decorators = {ast.unparse(d) for d in function.decorator_list}
    if 'classmethod' in decorators and positional:
        positional = positional[1:]
    elif positional and positional[0].arg in ('self', 'cls'):
        return {}  # Receiver binding is not implemented.
    result = {}
    for formal, actual in zip(positional, call.args):
        if isinstance(actual, ast.Starred):
            break
        result[formal.arg] = ast.unparse(actual)
    allowed = {a.arg for a in function.args.args + function.args.kwonlyargs}
    for kw in call.keywords:
        if kw.arg in allowed:
            if kw.arg in result:
                return {}  # Duplicate assignment is not a valid binding.
            result[kw.arg] = ast.unparse(kw.value)
    return result


def symbol_scope(file, symbol):
    return file + ':' + symbol if symbol.startswith('local ') else symbol


class AnswerIndex:
    def __init__(self, sources, raw):
        self.functions, self.calls = {}, {}
        occurrences, implementations = defaultdict(list), defaultdict(set)
        for doc in raw.get('documents', []):
            file = doc['relativePath']
            if file not in sources:
                continue
            for o in doc.get('occurrences', []):
                span = o.get('range', [])
                if len(span) == 3 and o.get('symbol'):
                    occurrences[file, span[0] + 1].append((span[1], span[2], symbol_scope(file, o['symbol']), o.get('symbolRoles', 0)))
            for info in doc.get('symbols', []):
                for relation in info.get('relationships', []):
                    if relation.get('isImplementation'):
                        implementations[symbol_scope(file, relation['symbol'])].add(symbol_scope(file, info['symbol']))
        pending = []
        for file, source in sorted(sources.items()):
            lines = source.splitlines()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                # ASCII-only coordinate matching avoids silently mixing AST
                # UTF-8 columns and potentially UTF-16 index coordinates.
                line = lines[fn.lineno - 1]
                if not line.isascii():
                    continue
                identities = [s for lo, hi, s, roles in occurrences[file, fn.lineno]
                              if roles & 1 and line[lo:hi] == fn.name]
                if len(identities) != 1:
                    continue
                symbol = identities[0]
                self.functions[symbol] = {'file': file, 'node': fn, 'line': fn.lineno,
                                          'abstract': any(ast.unparse(d).endswith('abstractmethod') for d in fn.decorator_list)}
                pending.append((symbol, file, fn, lines))
        for symbol, file, fn, lines in pending:
            own_calls = []
            # Nested bodies are not part of the enclosing call sequence.
            def visit(node):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    return
                if isinstance(node, ast.Call):
                    own_calls.append(node)
                for child in ast.iter_child_nodes(node):
                    visit(child)
            for statement in fn.body:
                visit(statement)
            resolved = []
            for call in own_calls:
                func, references = call.func, set()
                if isinstance(func, (ast.Name, ast.Attribute)) and func.lineno == func.end_lineno:
                    line = lines[func.lineno - 1]
                    name = func.id if isinstance(func, ast.Name) else func.attr
                    hi = func.end_col_offset
                    lo = hi - len(name)
                    if line.isascii():
                        references = {s for start, end, s, roles in occurrences[file, func.lineno]
                                      if not roles & 1 and start == lo and end == hi}
                targets = set()
                for reference in references:
                    if reference in self.functions:
                        targets.add((reference, 'indexed_reference'))
                    targets.update((s, 'implementation_candidate') for s in implementations[reference] if s in self.functions)
                resolved.append({'node': call, 'line': call.lineno, 'column': call.col_offset,
                                 'targets': sorted(targets), 'reference_symbols': sorted(references)})
            self.calls[symbol] = resolved

    def trace(self, symbol, parameter, depth=3, path=(), seen=frozenset()):
        if (symbol, parameter) in seen or depth < 0:
            return [], [{'reason': 'cycle_or_depth_limit', 'symbol': symbol, 'parameter': parameter}]
        fn = self.functions[symbol]['node']
        observation = observe_parameter(fn, parameter)
        certainty = observation['status'] in ('forwarded_syntax', 'unused_syntax', 'used_not_forwarded')
        step = {'symbol': symbol, 'parameter': parameter, 'observation': observation['status'],
                'relation': 'indexed_reference', 'uncertain': not certainty}
        path = (*path, step)
        children = []
        for link in observation['potential_call_links']:
            for call in self.calls[symbol]:
                if (call['line'], call['column']) != (link['line'], link['column']):
                    continue
                for target, relation in call['targets']:
                    bindings = bind_arguments(call['node'], self.functions[target]['node'])
                    for formal, actual in bindings.items():
                        if actual in link['actuals']:
                            children.append((target, formal, relation, link['uncertain']))
        if not children:
            return [{'symbol': symbol, 'parameter': parameter, 'observation': observation,
                     'path': list(path), 'abstract': self.functions[symbol]['abstract'],
                     'uncertain_path': any(s['uncertain'] for s in path)}], []
        answers, unresolved = [], []
        for target, formal, relation, link_uncertain in children:
            next_path = path[:-1] + (step | {'relation': relation, 'uncertain': step['uncertain'] or
                                           relation == 'implementation_candidate' or link_uncertain},)
            found, unknown = self.trace(target, formal, depth - 1, next_path, seen | {(symbol, parameter)})
            answers.extend(found)
            unresolved.extend(unknown)
        return answers, unresolved


def select_answers(base, head, index, diff, proposal, budget=800):
    if type(budget) is not int or budget < 0:
        raise ValueError('Invalid budget')
    _, paired = retrieve(base, head, diff, 'scip_delta', budget)
    candidates = paired['ranked_keys']
    changes = changed_ranges(diff)['head']
    questions = [q for h in proposal['hypotheses'] for q in h['questions']]
    forwarding = [q for q in questions if q['operation'] == 'forwarding']
    vocabulary = set(re.findall(r'[A-Za-z_][A-Za-z_0-9]*', ' '.join(
        ' '.join([q['question'], *q['symbols'], q['support_if'], q['refute_if']]) for q in forwarding)))
    answers, unresolved, anchors = [], [], []
    for symbol, f in index.functions.items():
        if not forwarding:
            break
        for call in index.calls[symbol]:
            if not set(range(call['node'].lineno, call['node'].end_lineno + 1)) & changes.get(f['file'], set()):
                continue
            for target, relation in call['targets']:
                for formal, actual in bind_arguments(call['node'], index.functions[target]['node']).items():
                    if actual not in vocabulary or not re.fullmatch(r'[A-Za-z_]\w*', actual):
                        continue
                    anchor = {'symbol': symbol, 'parameter': actual, 'relation': relation,
                              'observation': 'changed_call_argument', 'uncertain': relation == 'implementation_candidate'}
                    anchors.append(anchor)
                    found, unknown = index.trace(target, formal, path=(anchor,))
                    answers.extend(found)
                    unresolved.extend(unknown)
    # Rank evidence depth, not a direction of failure: safe forwarding and
    # unused parameters have identical priority. Unused does not mean defect.
    answers.sort(key=lambda a: (a['abstract'], a['uncertain_path'], -len(a['path']), a['symbol'], a['parameter']))
    preferred = []
    for answer in answers:
        f = index.functions[answer['symbol']]
        keys = [k for k in candidates if head.chunks[k].file == f['file'] and
                head.chunks[k].start <= f['node'].end_lineno and head.chunks[k].end >= f['line']]
        answer['candidate_keys'] = keys
        preferred.extend(k for k in keys if k not in preferred)
    # Use the full claim rank, not only the previously packed source subset.
    _, fallback = select(base, head, diff, proposal, 'claim_search', budget=10**9)
    order = preferred + [k for k in fallback['selection_order'] if k not in preferred]
    selected, skipped = [], []
    for key in order:
        if token_count(render_blocks(canonical_blocks(head.chunks, selected + [key]))) <= budget:
            selected.append(key)
        else:
            skipped.append(key)
    for answer in answers:
        answer['selected_keys'] = [k for k in answer['candidate_keys'] if k in selected]
        f = index.functions[answer['symbol']]
        covered = {line for k in answer['selected_keys'] for line in range(head.chunks[k].start, head.chunks[k].end + 1)}
        answer['full_function_selected'] = set(range(f['line'], f['node'].end_lineno + 1)) <= covered
    blocks = canonical_blocks(head.chunks, selected)
    return blocks, {'arm': 'answer_source', 'source_tokens': token_count(render_blocks(blocks)),
                    'candidate_keys': candidates, 'selection_order': selected, 'budget_skipped_keys': skipped,
                    'anchors': anchors, 'answer_candidates': answers, 'unresolved': unresolved,
                    'unsupported_questions': [q for q in questions if q['operation'] != 'forwarding'],
                    'runtime_verified': False, 'source_order': 'file/start/key'}
