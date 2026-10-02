"""Browser stdio native host; accepts only official PDF bytes from allowed extensions."""
import base64, contextlib, hashlib, json, os, re, struct, sys, time, uuid
from . import __version__, core

HOST_NAME='io.github.llrhook.tcg_order_printer'
PROTOCOL_VERSION=2
MAX_PDF=16*1024*1024
MAX_MESSAGE=24*1024*1024
ORDER=re.compile(r'^[A-Z0-9]{8}-[A-Z0-9]{6}-[A-Z0-9]{5}$')
TOKEN=re.compile(r'^[a-f0-9-]{36}$',re.I)
EXTENSION_ORIGIN=re.compile(r'^chrome-extension://([a-p]{32})/?$')

class Rejected(Exception): pass

def read_exact(stream,size):
    chunks=[]
    while size:
        block=stream.read(size)
        if not block: raise EOFError
        chunks.append(block);size-=len(block)
    return b''.join(chunks)

def handle(message,state=core.DEFAULT_STATE):
    if not isinstance(message,dict): raise Rejected('INVALID_MESSAGE')
    command=message.get('command')
    try: conf=core.load_config(state)
    except core.SetupRequired as error: raise Rejected(str(error))
    if command=='ping':
        if not core.printer_ready(conf['printer']): raise Rejected('PRINTER_QUEUE_UNAVAILABLE')
        return {'ok':True,'state':'ready','printer':conf['printer'],'helperVersion':__version__,'protocolVersion':PROTOCOL_VERSION,'clickIdempotency':'request-id'}
    if command!='print': raise Rejected('UNSUPPORTED_COMMAND')
    oid=message.get('orderId','');token=message.get('requestId','')
    if not isinstance(oid,str) or not ORDER.fullmatch(oid) or not isinstance(token,str) or not TOKEN.fullmatch(token): raise Rejected('INVALID_ORDER_REQUEST')
    # No path is accepted for live printing. Byte transport avoids Downloads
    # filesystem permissions and arbitrary local-file access entirely.
    data=message.get('pdfBase64')
    if not isinstance(data,str) or len(data)>MAX_MESSAGE: raise Rejected('PDF_BYTES_REQUIRED')
    try: raw=base64.b64decode(data,validate=True)
    except ValueError: raise Rejected('INVALID_PDF_ENCODING')
    if not raw.startswith(b'%PDF-') or len(raw)>MAX_PDF or b'%%EOF' not in raw[-1024:]: raise Rejected('INVALID_OR_INCOMPLETE_PDF')
    token=str(uuid.UUID(token))
    sha=hashlib.sha256(raw).hexdigest()
    core.prune(state,conf['retention_days'])
    incoming=state/'inputs';incoming.mkdir(parents=True,exist_ok=True,mode=0o700)
    source=incoming/f'TCGplayer_PackingSlips_{sha[:24]}.pdf'
    if not source.exists():
        temporary=incoming/f'.{sha[:24]}-{uuid.uuid4()}.tmp'
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(raw)
            os.replace(temporary,source)
        finally:
            if temporary.exists():temporary.unlink()
    try: groups=core.parse(source)
    except ValueError: raise Rejected('UNSUPPORTED_PACKING_SLIP')
    if [g['id'] for g in groups]!=[oid]: raise Rejected('PDF_ORDER_MISMATCH')
    pages=sum(len(g['pages'])+1 for g in groups)
    output=state/'labels'/f'{oid}-{sha[:16]}-{token[:8]}.pdf'
    # A fresh explicit click has a new request ID and may print a completed
    # order again. The same ID replay is idempotent, including after a crash.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            job=core.process(source,state,output,expect=oid,request_id=token)
    except core.PrintGuardError as error: raise Rejected(str(error))
    if not job:
        with core.connection(state) as db:
            row=db.execute('select state,job from print_requests where request_id=?',(token,)).fetchone()
        if not row or row[0] in {'submitting','uncertain','printer_error'}: raise Rejected('PREVIOUS_SUBMISSION_NEEDS_QUEUE_CHECK')
        return {'ok':True,'state':'submitted' if row[0]=='submitted' else 'duplicate','orderId':oid,'pages':pages,'job':row[1],'requestId':token,'reusedRequest':True}
    deadline=time.monotonic()+18
    while time.monotonic()<deadline:
        try:
            jobstate=core.job_status(job)
            if jobstate.get('job-state')==9:
                core.update_job_state(state,job,'completed')
                return {'ok':True,'state':'completed','orderId':oid,'pages':pages,'job':job,'requestId':token,'sheets':jobstate.get('job-media-sheets-completed')}
            if jobstate.get('job-state') in {7,8}:
                core.update_job_state(state,job,'printer_error')
                raise Rejected('PRINTER_JOB_CANCELLED_OR_ABORTED')
        except Rejected: raise
        except Exception: break
        time.sleep(.75)
    return {'ok':True,'state':'submitted','orderId':oid,'pages':pages,'job':job,'requestId':token}

def origin_allowed(origin,state):
    match=EXTENSION_ORIGIN.fullmatch(origin or '')
    if not match: return False
    try: conf=json.loads((state/'config.json').read_text())
    except (FileNotFoundError,ValueError): return False
    return match.group(1) in conf.get('extension_ids',[])

def serve(origin,stdin=None,stdout=None,state=core.DEFAULT_STATE):
    inp=stdin or sys.stdin.buffer;out=stdout or sys.stdout.buffer
    if not origin_allowed(origin,state):
        return 2
    while True:
        try:
            header=inp.read(4)
            if not header:return 0
            if len(header)!=4:raise EOFError
            length=struct.unpack('=I',header)[0]
            if not 0<length<=MAX_MESSAGE:raise Rejected('MESSAGE_TOO_LARGE')
            message=json.loads(read_exact(inp,length).decode('utf-8'))
            result=handle(message,state)
        except EOFError:return 1
        except Rejected as error:result={'ok':False,'error':str(error)}
        except Exception as error:
            # Private addresses, file contents, and exception tracebacks never
            # enter protocol responses or persistent logs.
            print('Native print helper error: '+type(error).__name__,file=sys.stderr,flush=True)
            result={'ok':False,'error':'LOCAL_PRINT_HELPER_ERROR'}
        raw=json.dumps(result,separators=(',',':')).encode('utf-8')
        out.write(struct.pack('=I',len(raw)));out.write(raw);out.flush()
        # An oversized frame was not consumed, so the stream cannot be resynced.
        if result.get('error')=='MESSAGE_TOO_LARGE':return 1
