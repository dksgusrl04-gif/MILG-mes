"""Durable file delivery: write .tmp, then rename to .ready.json atomically.

Files stay in customer storage. Successful content is idempotent across restarts.
Failure may commit a prefix; corrected/retried files safely reuse cycle/source IDs.
"""
import hashlib
import json


def ingest(engine):
    from .learning import LearningManager, encoded, stamp
    inbox=engine.state/'inbox';inbox.mkdir(exist_ok=True)
    results=[]
    for path in sorted(inbox.glob('*.ready.json'))[:100]:
        if path.is_symlink() or not path.resolve().is_relative_to(inbox.resolve()):continue
        if path.stat().st_size>20_000_000:
            results.append(dict(file=path.name,status='rejected',error='File exceeds 20 MB'));continue
        raw=path.read_bytes();checksum=hashlib.sha256(raw).hexdigest()
        with engine.db() as db:
            prior=db.execute('SELECT result FROM learning_inbox WHERE hash=?',(checksum,)).fetchone()
        if prior and json.loads(prior[0]).get('status')=='complete':continue
        result=dict(file=path.name,status='received',committed=0)
        try:
            body=json.loads(raw.decode('utf-8-sig'))
            if not isinstance(body,dict) or body.get('format')!='milg_inbox_v1':raise ValueError('Expected milg_inbox_v1 envelope')
            items=body.get('items')
            if not isinstance(items,list) or not 1<=len(items)<=1000 or not all(isinstance(i,dict) for i in items):
                raise ValueError('items must contain 1..1000 objects')
            kind=body.get('kind')
            if kind not in ('cycles','feedback','lot_close'):raise ValueError('Unknown inbox kind')
            if kind=='feedback' and any(not item.get('source_id') for item in items):
                raise ValueError('Each inspection needs a unique stable source_id (new ID for revision)')
            for item in items:
                if kind=='cycles':engine.submit(item)
                elif kind=='feedback':engine.feedback(**item)
                else:LearningManager(engine).close_lot(**item)
                result['committed']+=1
            result['status']='complete'
        except (ValueError,TypeError,KeyError) as exc:result.update(status='rejected',error=str(exc))
        # Unexpected I/O/database errors propagate and retry on the next worker run.
        with engine.db() as db:
            db.execute('INSERT OR REPLACE INTO learning_inbox VALUES(?,?,?,?)',(checksum,path.name,stamp(),encoded(result)))
        results.append(result)
    return results
