"""Rank a frozen PR's source blocks locally and budget complete evidence bundles."""
from collections import Counter
from itertools import product
import re
import threading

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from experiment import ROOT, tokens

READ_CAP = 6500
TOP_K = 3
WORD = re.compile(r'[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+')
ROLE_WORDS = {
    'change': set(),
    'caller': {'return', 'call', 'await', 'use', 'invoke', 'consumer', 'handler'},
    'implementation': {'def', 'func', 'function', 'class', 'return', 'impl', 'method'},
    'failure': {'except', 'catch', 'error', 'throw', 'raise', 'finally', 'fail', 'retry', 'cleanup', 'timeout'},
    'configuration': {'config', 'default', 'option', 'env', 'setting', 'schema'},
    'test': {'test', 'expect', 'assert', 'mock', 'fixture'},
    'counterevidence': {'if', 'guard', 'default', 'fallback', 'check', 'validate'},
    'other': set(),
}


def terms(text):
    return {word.lower() for word in WORD.findall(text)}


class Encoder:
    def __init__(self):
        self.tokenizer = Tokenizer.from_file(str(ROOT/'model/tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding()
        self.tokenizer_lock = threading.Lock()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 3
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(ROOT/'model/model.onnx'), sess_options=options,
                                            providers=['CPUExecutionProvider'])
        self.inputs = {item.name for item in self.session.get_inputs()}

    def encode(self, texts):
        batches = []
        for offset in range(0, len(texts), 12):
            with self.tokenizer_lock:
                encoded = self.tokenizer.encode_batch(texts[offset:offset+12])
            arrays = {
                'input_ids': np.asarray([item.ids for item in encoded],dtype=np.int64),
                'attention_mask': np.asarray([item.attention_mask for item in encoded],dtype=np.int64),
                'token_type_ids': np.asarray([item.type_ids for item in encoded],dtype=np.int64),
            }
            vectors = self.session.run(None,{name:arrays[name] for name in self.inputs})[0]
            if vectors.ndim==3:
                vectors = vectors[:,0,:]
            batches.append(vectors/np.maximum(np.linalg.norm(vectors,axis=1,keepdims=True),1e-12))
        return np.concatenate(batches)


def document(entry):
    # Use the public file/line label and code excerpt, without repeated diff headers.
    rows = [r for r in entry['text'].splitlines()
            if not r.startswith(('diff --git ', 'index ', '--- ', '+++ ', '<<< '))]
    return entry['path']+' '+entry['label']+'\n'+'\n'.join(rows)


def role_rank(question, role, identifiers, entries, similarities, docs, resolve):
    hints = {identifier for identifier,ok in resolve(role['hints'],entries).items() if ok}
    query_words = terms(question+' '+role['need'])
    role_words = ROLE_WORDS[role['role']]
    rows = []
    for identifier, similarity, words in zip(identifiers,similarities,docs):
        entry = entries[identifier]
        if role['role']=='change' and entry['kind']!='hunk':
            continue
        lexical = len(query_words&words)/max(1,len(query_words))
        cue = min(1,len(role_words&words)/3)
        score = .75*float(similarity)+.20*lexical+.05*cue+.18*(identifier in hints)
        rows.append(dict(id=identifier,score=round(score,6),cosine=round(float(similarity),6),
                         explicit_hint=identifier in hints))
    return sorted(rows,key=lambda item:(-item['score'],item['id']))[:TOP_K]


def lookup_bundles(questions, entries, number, encoder, vectors, resolve, already_visible=()):
    """Choose one source candidate per needed role, favoring whole questions under the same cap."""
    identifiers = list(entries)
    docs = [terms(document(entry)) for entry in entries.values()]
    visible = set(already_visible)
    delivered, receipts, blocks = set(), [], []
    remaining = READ_CAP
    requests = [(q['question'],role) for q in questions for role in q['roles']]
    if requests:
        query_vectors = encoder.encode([f'Represent this sentence for searching relevant passages: {role["role"]}. '
                                        f'{role["need"]} {question}' for question,role in requests])
        matches = iter(query_vectors @ vectors.T)
    else:
        matches = iter(())
    for index, question in enumerate(questions,1):
        ranks = [role_rank(question['question'],role,identifiers,entries,next(matches),docs,resolve)
                 for role in question['roles']]
        available = visible|delivered
        candidates = {row['id'] for rank in ranks for row in rank}
        rendered = {i:f'<<< Lookup {i} >>>\n{entries[i]["text"]}' for i in candidates-available}
        prices = {i:tokens(text)+2 for i,text in rendered.items()}
        best = None
        # Missing slots remain visible. Do not substitute an ID outside the role's shortlist.
        for combination in product(*[rank+[None] for rank in ranks]):
            selected = {row['id'] for row in combination if row}
            cost = sum(prices.get(i,0) for i in selected)
            if cost > remaining:
                continue
            filled = sum(row is not None for row in combination)
            # Prefer distinct evidence and favor relevant source among equally complete bundles.
            quality = sum(row['score'] for row in combination if row)-.16*(filled-len(selected))
            rank = (filled,quality,-cost)
            if best is None or rank>best[0]:
                best = rank,combination,cost
        _, chosen, cost = best
        previous = set(delivered)
        roles = []
        for role, shortlist, row in zip(question['roles'],ranks,chosen):
            identifier = row['id'] if row else ''
            if not row:
                status = 'budget_deferred' if shortlist else 'no_candidate'
            elif identifier in visible:
                status = 'already_visible'
            elif identifier in previous or identifier in delivered:
                status = 'shared_read'
            else:
                blocks.append(rendered[identifier])
                delivered.add(identifier)
                status = 'delivered'
            roles.append(dict(role=role['role'],need=role['need'],target=identifier,result=status,
                              hints=role['hints'],shortlist=shortlist))
        remaining -= cost
        receipts.append(dict(id=f'Q{number}.{index}',question=question['question'],perspective=question['perspective'],roles=roles))
    source = '\n\n'.join(blocks)
    assert tokens(source)<=READ_CAP
    return dict(receipts=receipts,source=source,targets=sorted(delivered),reference_tokens=READ_CAP-remaining)


def compact_receipts(receipts):
    return [dict(q,roles=[{k:v for k,v in role.items() if k not in ('shortlist','hints')} for role in q['roles']])
            for q in receipts]
