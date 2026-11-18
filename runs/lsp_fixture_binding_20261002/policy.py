"""Human-selected binding route; complete selected statements, no line truncation."""
import ast
import hashlib

ROUTES = {
    '_pytest/unittest.py': ['pytest_pycollect_makeitem', 'UnitTestCase.collect', 'UnitTestCase._inject_setup_teardown_fixtures', '_make_xunit_fixture', '_is_skipped'],
    '_pytest/fixtures.py': ['fixture', 'FixtureFunctionMarker', 'getfixturemarker', 'FixtureDef.__init__', 'FixtureManager.parsefactories'],
    '_pytest/python.py': ['_showfixtures_main'],
}


def declarations(source):
    result = {}
    def walk(nodes, prefix=''):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Overloads precede their real implementation; retain last declaration.
                result[prefix + node.name] = node
                if isinstance(node, ast.ClassDef):
                    walk(node.body, prefix + node.name + '.')
    walk(ast.parse(source).body)
    return result


def select(source, symbol):
    node = declarations(source)[symbol]
    lines = set()
    selected = []
    def span(n):
        lines.update(range(n.lineno, n.end_lineno + 1))
        selected.append({'kind': type(n).__name__, 'start': n.lineno, 'end': n.end_lineno, 'text': ast.get_source_segment(source, n)})
    def header(n):
        lines.update(range(n.lineno, n.body[0].lineno))
    def signature(n):
        start = min([n.lineno] + [d.lineno for d in n.decorator_list])
        lines.update(range(start, n.body[0].lineno))
    def has(n, text):
        return text in ast.unparse(n)
    signature(node)
    body = node.body
    if symbol == 'pytest_pycollect_makeitem':
        for n in body: span(n)
    elif symbol == 'UnitTestCase.collect':
        for n in body:
            if getattr(n, 'lineno', 0) >= next(n.lineno for n in body if has(n, 'loader =')): break
            span(n)
    elif symbol == 'UnitTestCase._inject_setup_teardown_fixtures':
        for n in body:
            if has(n, 'method_fixture ='): break
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)): span(n)
    elif symbol == '_make_xunit_fixture':
        for n in body:
            if isinstance(n, ast.FunctionDef): signature(n)
            elif has(n, 'setup =') or has(n, 'teardown =') or (isinstance(n, ast.If) and has(n.test, 'setup is None')) or isinstance(n, ast.Return): span(n)
    elif symbol == 'fixture':
        for n in body:
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)): span(n)
    elif symbol in ['FixtureFunctionMarker', 'getfixturemarker', '_is_skipped']:
        for n in body: span(n)
    elif symbol == 'FixtureDef.__init__':
        for n in body:
            if isinstance(n, ast.Assign) and any(has(n.targets[0], text) for text in ['self._fixturemanager', 'self.baseid', 'self.has_location', 'self.func', 'self.argname']): span(n)
    elif symbol == 'FixtureManager.parsefactories':
        for n in body:
            if isinstance(n, ast.For):
                header(n)
                for child in n.body:
                    if not has(child, 'marker.autouse'): span(child)
            elif not has(n, 'autousenames'): span(n)
    elif symbol == '_showfixtures_main':
        for n in body:
            if isinstance(n, ast.For) and has(n.iter, 'available'):
                header(n)
                for child in n.body:
                    if (isinstance(child, ast.If) and has(child.test, 'verbose <= 0')) or (isinstance(child, ast.Expr) and has(child, 'tw.write(argname')): span(child)
            elif not has(n, 'currentmodule ='): span(n)
    else: raise ValueError(symbol)
    # Include module import for the attr-generated marker constructor.
    if symbol == 'FixtureFunctionMarker':
        for n in ast.parse(source).body:
            if isinstance(n, ast.Import) and any(a.name == 'attr' for a in n.names): span(n)
    return lines, selected


def render(sources, selected_lines):
    from ace_review.protocol_hypothesis import mask_source
    chunks = []
    for file, lines in sorted(selected_lines.items()):
        key = 'E-' + hashlib.sha256(('binding-route:head:' + file).encode()).hexdigest()[:12]
        chunks.append(f'[{key}] head {file}:{min(lines)}-{max(lines)} PARTIAL AST STATEMENTS; omitted code unknown; selected statements are complete')
        previous = None
        masked = mask_source(sources[file]).splitlines()
        for line in sorted(lines):
            if previous is not None and previous + 1 != line: chunks.append('... omitted lines ...')
            chunks.append(f'{line}: {masked[line - 1]}')
            previous = line
    return '\n'.join(chunks)
