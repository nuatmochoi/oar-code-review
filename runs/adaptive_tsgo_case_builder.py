"""Build a repository-disjoint, author-known counterfactual naming pair."""
import difflib
import hashlib
import json
from pathlib import Path
import subprocess

from ace_review.context_necessity import freeze

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / 'data/repos/microsoft__typescript-go'
HEAD = '892b4a3f6b701352047ceb019fc0683ab1146bb3'
CONVERT = 'internal/fourslash/_scripts/convertFourslash.mts'
PRODUCER = 'internal/fourslash/_scripts/makeManual.mts'


def blob(commit, path):
    result = subprocess.run(['git', 'show', f'{commit}:{path}'], cwd=REPO,
                            capture_output=True, text=True, check=True)
    return result.stdout


def target_base(head):
    replacements = [
        ('const manualTestsPath = path.join(import.meta.dirname, "manualTests.txt");\n', ''),
        ('const manualOutputDir = path.join(import.meta.dirname, "../", "tests", "manual");\n', ''),
        ('function getManualTests(): Set<string> {\n    const manualTestsList = fs.readFileSync(manualTestsPath, "utf-8").split("\\n").map(line => line.trim()).filter(line => line.length > 0);\n    return new Set(manualTestsList);\n}\n\n', ''),
        ('    fs.mkdirSync(manualOutputDir, { recursive: true });\n', ''),
        ('    parseTypeScriptFiles(getFailingTests(), getManualTests(), stradaFourslashPath);',
         '    parseTypeScriptFiles(getFailingTests(), stradaFourslashPath);'),
        ('function parseTypeScriptFiles(failingTests: Set<string>, manualTests: Set<string>, folder: string): void {',
         'function parseTypeScriptFiles(failingTests: Set<string>, folder: string): void {'),
        ('            parseTypeScriptFiles(failingTests, manualTests, filePath);',
         '            parseTypeScriptFiles(failingTests, filePath);'),
        ('                const testName = test.name[0].toUpperCase() + test.name.substring(1);\n                // Skip generation if test is in manual tests list\n                if (manualTests.has(testName)) {\n                    return;\n                }\n', ''),
    ]
    output = head
    for old, new in replacements:
        if old not in output:
            raise ValueError('Expected target fragment is absent')
        output = output.replace(old, new, 1)
    return output


def safe_producer(unsafe):
    helper = '''function normalizeManualTestName(fileName: string): string {
    const base = path.basename(fileName, "_test.go");
    return base[0].toUpperCase() + base.substring(1);
}

'''
    if 'function main() {' not in unsafe:
        raise ValueError('Producer main is absent')
    output = unsafe.replace('function main() {', helper + 'function main() {', 1)
    old = '''    if (!manualTests.includes(testName)) {
        manualTests.push(testName);
'''
    new = '''    const storedTestName = normalizeManualTestName(testName);
    if (!manualTests.includes(storedTestName)) {
        manualTests.push(storedTestName);
'''
    if old not in output:
        raise ValueError('Producer storage fragment is absent')
    return output.replace(old, new, 1)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def build(root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    head_consumer = blob(HEAD, CONVERT)
    base_consumer = target_base(head_consumer)
    unsafe = blob(HEAD, PRODUCER)
    safe = safe_producer(unsafe)
    diff = ''.join(difflib.unified_diff(base_consumer.splitlines(True),
                                        head_consumer.splitlines(True),
                                        f'a/{CONVERT}', f'b/{CONVERT}'))
    if not diff or PRODUCER in diff:
        raise ValueError('Invalid target-only diff')
    cells = {}
    for variant, producer in (('safe', safe), ('unsafe', unsafe)):
        for revision, consumer in (('base', base_consumer), ('head', head_consumer)):
            cell = root / f'{variant}_{revision}'
            files = {f'src/{CONVERT}': consumer, f'src/{PRODUCER}': producer}
            for name, content in files.items():
                path = cell / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            manifest = {name: sha_bytes(content.encode()) for name, content in files.items()}
            (cell / 'index_audit.json').write_text(json.dumps({'source_sha256': manifest}, indent=2))
            cells[f'{variant}_{revision}'] = str(cell)
    (root / 'target.diff').write_text(diff)
    line = next(i for i, text in enumerate(head_consumer.splitlines(), 1)
                if 'manualTests.has(testName)' in text)
    labels = {'unsafe': {'defect': True, 'file': CONVERT, 'line': line,
                         'mechanism': 'producer stores generated filename while consumer looks up capitalized test name'},
              'safe': {'defect': False}}
    (root / 'private_labels.json').write_text(json.dumps(labels, indent=2))
    result = {'repository': 'microsoft/typescript-go', 'source_commit': HEAD,
              'target_diff_sha256': sha_bytes(diff.encode()), 'cells': cells,
              'counterfactual': 'Consumer-only target diff; producer moved to unchanged companion',
              'blind_holdout': False}
    (root / 'case.json').write_text(json.dumps(result, indent=2))
    freeze(root)
    return result


if __name__ == '__main__':
    print(json.dumps(build(ROOT / 'runs/adaptive_tsgo_case1'), indent=2))
