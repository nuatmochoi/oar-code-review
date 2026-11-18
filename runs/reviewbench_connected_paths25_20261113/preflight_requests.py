"""Build exact final requests without network or writes to review result slots."""
import json
import experiment as e
class Captured(Exception):pass
def main():
    original_write,original_call=e.write,e.call
    pending=[];results=[]
    def capture(body,dest):
        pending.append(dict(slot=str(dest),request_reference_tokens=e.tokens(body),
                            max_output_tokens=body['max_output_tokens'],tools=body['tools']))
        raise Captured()
    e.write=lambda *a,**kw:None;e.call=capture
    try:
        for ref in e.load(e.ROOT/'public/manifest.json'):
            packet=e.load(ref['path'])
            for arm in e.ARMS:
                try:e.finalize(packet,arm)
                except Captured:results.append(dict(index=packet['index'],arm=arm,valid=True,**pending.pop()))
                except Exception as exc:results.append(dict(index=packet['index'],arm=arm,valid=False,error=str(exc)))
    finally:e.write,e.call=original_write,original_call
    original_write(e.ROOT/'audit/final-request-preflight.json',dict(results=results,all_valid=all(x['valid']for x in results),new_model_calls=0))
    print(json.dumps(dict(requests=len(results),valid=sum(x['valid']for x in results),failures=[x for x in results if not x['valid']]),ensure_ascii=False))
    if not all(x['valid']for x in results):raise RuntimeError('Final request preflight failed')
if __name__=='__main__':main()
