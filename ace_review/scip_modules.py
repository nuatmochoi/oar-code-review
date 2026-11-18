"""Index the root and nearest owning modules of changed Go files. No repair."""
from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from .scip_batch import index_revision


def affected_module_roots(tracked: list[str], changed: list[str]) -> tuple[list[str], list[str]]:
    roots={str(PurePosixPath(p).parent) for p in tracked if p=='go.mod' or p.endswith('/go.mod')}
    selected={'.'} if '.' in roots else set()
    missing=[]
    for name in sorted(changed):
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Unsafe changed path')
        owners=[r for r in roots if path.is_relative_to(PurePosixPath(r))]
        if owners:
            selected.add(max(owners,key=lambda r:len(PurePosixPath(r).parts)))
        else:
            missing.append(name)
    return sorted(selected),missing


def index_modules(repo: Path, commit: str, changed: list[str], cache_root: Path,
                  indexer: str, exporter: str, timeout: int) -> tuple[dict | None, dict]:
    tracked=subprocess.check_output(['git','ls-tree','-r','--name-only',commit],cwd=repo,text=True).splitlines()
    roots,missing=affected_module_roots(tracked,changed)
    documents={};statuses=[]
    for root in roots:
        raw,status=index_revision(repo,commit,cache_root,indexer,exporter,timeout,module_root=root)
        statuses.append(status|{'module_root':root})
        if raw:
            for d in raw.get('documents',[]):
                # Module version normalization belongs to the producing module,
                # not all modules which happen to be in the same repository.
                document=d|{'indexModule':status['module']}
                path=document['relativePath']
                if path in documents and documents[path] != document:
                    raise ValueError(f'Conflicting module source ownership: {path}')
                documents[path]=document
    failed=[s['module_root'] for s in statuses if not s['available']]
    status={'available':bool(documents),'scope_complete':bool(roots) and not failed and not missing,
            'commit':commit,'selected_module_roots':roots,'unowned_changed_files':missing,
            'failed_module_roots':failed,'modules':statuses,'documents':len(documents),
            'module':'per-document-module','dependency_repair':False}
    return ({'documents':list(documents.values())} if documents else None),status
