"""Native TCGplayer packing slip + matched address label, submitted to CUPS."""
import collections, contextlib, datetime, hashlib, http.client, io, json, os, re, socket, sqlite3, struct, subprocess, time, uuid
from pathlib import Path
import pdfplumber
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

DEFAULT_STATE = Path.home() / 'Library/Application Support/TCGOrderPrinter'
ORDER = re.compile(r'Order\s*Number\s*:\s*([A-Z0-9]{8}-[A-Z0-9]{6}-[A-Z0-9]{5})', re.I)
PAGINATION = re.compile(r'Page\s*(\d+)\s*of\s*(\d+)', re.I)
CITY = re.compile(r'^.+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?$')
# 4x6 in label media, in PDF points.
LABEL_W, LABEL_H = 288, 432
DEFAULT_LP_OPTIONS = ['media=w288h432', 'orientation-requested=3', 'sides=one-sided']
DEFAULT_RETENTION_DAYS = 7

class SetupRequired(RuntimeError): pass
class PrintGuardError(ValueError): pass

def log(event, **fields):
    print(json.dumps(dict(time=datetime.datetime.now().isoformat(timespec='seconds'), event=event, **fields)), flush=True)

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

@contextlib.contextmanager
def connection(state):
    # Writers commit explicitly; leaving the block always closes the handle.
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(state/'ledger.sqlite3', timeout=30)
    try:
        db.execute('create table if not exists orders (id text primary key, state text, hash text, job text, output text)')
        db.execute('create table if not exists print_requests (request_id text primary key, order_id text, state text, hash text, job text, output text, created_at text)')
        db.execute('create table if not exists print_history (order_id text, job text, state text, hash text, output text, recorded_at text, primary key(order_id,job))')
        yield db
    finally:
        db.close()

@contextlib.contextmanager
def locked(state):
    import fcntl
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(state/'process.lock', 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield

def address(page):
    # Geometry isolates the native address column; a small tolerance recovers
    # spaces that default PDF text extraction merges in TCGplayer's templates.
    words = page.extract_words(x_tolerance=.5)
    ship = next((w for w in words if w['text']=='Ship' and w['top']<120), None)
    if ship:
        y = ship['bottom']+2
        stop = min(w['top'] for w in words if w['text']=='Order' and w['top']>y)
        raw = page.crop((30,y,280,stop-2)).extract_text(x_tolerance=.5)
    else:
        heading = next((w for w in words if w['text']=='Shipping' and 120<w['top']<300), None)
        if heading and any(w['text']=='Address:' and abs(w['top']-heading['top'])<2 for w in words):
            raw = page.crop((30,heading['bottom']+1,280,285)).extract_text(x_tolerance=.5)
        elif any(w['text']=='Line' and 220<w['top']<300 for w in words):
            raw = page.crop((95,145,400,240)).extract_text(x_tolerance=.5)
        else:
            raise ValueError('Unsupported address layout; use native Print Default')
    lines = [s.strip() for s in (raw or '').splitlines() if s.strip()]
    # Stop precisely at the city line and retain an optional country line.
    city = next((i for i,s in enumerate(lines) if CITY.fullmatch(s)), None)
    if city is None or city<2 or city>5:
        raise ValueError('Address must contain recipient, street and US city/state/ZIP')
    result = lines[:city+1]
    if len(lines)>city+1 and lines[city+1].upper() in {'UNITED STATES','UNITED STATES OF AMERICA','USA','US'}:
        result.append(lines[city+1])
    if any(ORDER.search(s) or 'Quantity' in s or 'Shipping Method' in s for s in result):
        raise ValueError('Address block contains non-address text')
    return result

def parse(path):
    with open(path, 'rb') as stream:
        stream.seek(max(0,Path(path).stat().st_size-1024))
        if b'%%EOF' not in stream.read():
            raise ValueError('Download is incomplete: missing PDF end marker')
    groups = collections.OrderedDict()
    with pdfplumber.open(path) as pdf:
        for index,page in enumerate(pdf.pages):
            text = page.extract_text(x_tolerance=.5) or ''
            ids = set(ORDER.findall(text))
            if len(ids)!=1:
                raise ValueError(f'Page {index+1} does not identify exactly one order')
            oid = ids.pop().upper()
            pg = PAGINATION.findall(text)
            if len(pg)!=1:
                raise ValueError(f'Page {index+1} does not identify its native page count')
            n,total = map(int,pg[0])
            group = groups.setdefault(oid,dict(id=oid,pages=[],pagination=[],address=None))
            group['pages'].append(index)
            group['pagination'].append((n,total))
            if n==1:
                if group['address'] is not None:
                    raise ValueError('Duplicate order first page in export')
                group['address'] = address(page)
        for g in groups.values():
            nums = g['pagination']
            total = nums[0][1]
            if nums != [(n,total) for n in range(1,total+1)] or g['address'] is None:
                raise ValueError('Missing, repeated or out-of-order native continuation page')
    return list(groups.values())

def label(lines, return_lines, oid):
    stream = io.BytesIO()
    c = canvas.Canvas(stream, pagesize=(LABEL_W,LABEL_H), pageCompression=1)
    # Landscape content inside the same portrait media box as the native slip.
    c.translate(LABEL_W,0); c.rotate(90)
    y=266
    for i,s in enumerate(return_lines):
        c.setFont('Helvetica-Bold' if i==0 else 'Helvetica',11)
        c.drawString(18,y,s); y-=14
    c.setLineWidth(.5); c.rect(350,217,64,53)
    c.setFont('Helvetica',8); c.drawCentredString(382,241,'AFFIX STAMP')
    size=20
    while max(stringWidth(s,'Helvetica-Bold' if i==0 else 'Helvetica',size) for i,s in enumerate(lines))>388 and size>11:
        size-=.5
    if max(stringWidth(s,'Helvetica-Bold' if i==0 else 'Helvetica',size) for i,s in enumerate(lines))>388:
        raise ValueError('Address exceeds printable label width')
    y=153
    for i,s in enumerate(lines):
        c.setFont('Helvetica-Bold' if i==0 else 'Helvetica',size)
        c.drawString(22,y,s); y-=size*1.25
    if y<22: raise ValueError('Address exceeds printable label height')
    c.setFont('Helvetica',7); c.drawString(22,15,'Order '+oid)
    c.showPage(); c.save(); stream.seek(0)
    return PdfReader(stream).pages[0]

def build(source, output, groups, config):
    reader=PdfReader(source); writer=PdfWriter()
    for g in groups:
        for index in g['pages']:
            p=reader.pages[index]
            if p.rotation: p.transfer_rotation_to_content()
            w,h=float(p.mediabox.width),float(p.mediabox.height)
            scale=min((LABEL_W-6)/w,(LABEL_H-6)/h)
            x,y=(LABEL_W-w*scale)/2,(LABEL_H-h*scale)/2
            blank=writer.add_blank_page(width=LABEL_W,height=LABEL_H)
            blank.merge_transformed_page(p,Transformation().scale(scale).translate(x,y))
        writer.add_page(label(g['address'], config['return_address'],g['id']))
    output=Path(output); output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix('.tmp')
    with open(temp,'wb') as stream: writer.write(stream)
    os.replace(temp,output)
    os.chmod(output,0o600)
    return output

def validate_config(conf):
    if not isinstance(conf.get('printer'),str) or not conf['printer']: raise SetupRequired('PRINTER_NOT_CONFIGURED')
    lines=conf.get('return_address')
    if not isinstance(lines,list) or not 2<=len(lines)<=4 or not all(isinstance(s,str) and s.strip() for s in lines):
        raise SetupRequired('RETURN_ADDRESS_NOT_CONFIGURED')
    return conf

def load_config(state):
    try: conf=json.loads((state/'config.json').read_text())
    except FileNotFoundError: raise SetupRequired('SETUP_REQUIRED')
    conf.setdefault('lp_options',DEFAULT_LP_OPTIONS)
    conf.setdefault('retention_days',DEFAULT_RETENTION_DAYS)
    return validate_config(conf)

def save_config(state,conf):
    validate_config(conf)
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=state/'config.json.tmp'
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream: json.dump(conf,stream,indent=2)
    os.replace(temp,state/'config.json')

def prune(state,days):
    # Slips and labels contain buyer addresses; keep them only long enough to
    # reprint or investigate a recent job.
    cutoff=time.time()-days*86400
    for folder in ('inputs','labels'):
        for path in (state/folder).glob('*.pdf'):
            try:
                if path.stat().st_mtime<cutoff: path.unlink()
            except FileNotFoundError: pass

def mark_job_state(db,job,newstate):
    db.execute('update orders set state=? where job=?',(newstate,job))
    db.execute('update print_requests set state=? where job=?',(newstate,job))
    db.execute('update print_history set state=? where job=?',(newstate,job))

def update_job_state(state,job,newstate):
    with locked(state),connection(state) as db:
        mark_job_state(db,job,newstate);db.commit()

def require_finished_prior_jobs(db,oid):
    # New explicit clicks may repeat completed orders, but cannot start a
    # second job while any prior submission for that order is unresolved.
    rows=db.execute('select state,job from orders where id=?',(oid,)).fetchall()
    rows+=db.execute("select state,job from print_requests where order_id=? and state in ('submitting','uncertain','submitted','printer_error')",(oid,)).fetchall()
    for oldstate,job in set(rows):
        if oldstate=='completed': continue
        if oldstate=='submitted' and job:
            try: status=job_status(job)
            except Exception: raise PrintGuardError('PREVIOUS_SUBMISSION_NEEDS_QUEUE_CHECK')
            if status.get('job-state')==9:
                mark_job_state(db,job,'completed');continue
        raise PrintGuardError('PREVIOUS_SUBMISSION_NEEDS_QUEUE_CHECK')

def submit(path,conf,title):
    args=['/usr/bin/lp','-d',conf['printer'],'-t',title,'-n','1']
    for option in conf['lp_options']: args+=['-o',option]
    result=subprocess.run(args+[str(path)],capture_output=True,text=True,timeout=45)
    match=re.search(r'request id is (\S+)',result.stdout)
    if result.returncode or not match: raise RuntimeError('CUPS did not confirm a job handle')
    return match.group(1)

def process(source,state,output,expect,request_id):
    """Print one explicitly requested order; returns the CUPS job, or None on replay."""
    source=Path(source); sha=digest(source)
    try: request_id=str(uuid.UUID(request_id))
    except (ValueError,TypeError,AttributeError): raise PrintGuardError('INVALID_PRINT_REQUEST')
    with locked(state), connection(state) as db:
        conf=load_config(state)
        groups=parse(source)
        if not expect or [g['id'] for g in groups]!=[expect.upper()]:
            raise PrintGuardError('PDF_ORDER_MISMATCH')
        oid=groups[0]['id']
        existing=db.execute('select order_id from print_requests where request_id=?',(request_id,)).fetchone()
        if existing:
            if existing[0]!=oid: raise PrintGuardError('REQUEST_ID_ORDER_MISMATCH')
            log('request_replay_skipped',request_id=request_id[:8]);return None
        require_finished_prior_jobs(db,oid)
        out=Path(output)
        build(source,out,groups,conf)
        now=datetime.datetime.now().isoformat(timespec='seconds')
        # Archive the prior known job before replacing the latest-order row.
        old=db.execute('select state,hash,job,output from orders where id=?',(oid,)).fetchone()
        if old and old[2]:
            db.execute('insert or ignore into print_history values (?,?,?,?,?,?)',(oid,old[2],old[0],old[1],old[3],now))
        db.execute('insert or replace into orders values (?,?,?,?,?)',(oid,'submitting',sha,None,str(out)))
        db.execute('insert into print_requests values (?,?,?,?,?,?,?)',(request_id,oid,'submitting',sha,None,str(out),now))
        # Intent for both the order and request is durable BEFORE invoking LP.
        db.commit()
        try:
            job=submit(out,conf,f'TCG Order Printer {oid} request {request_id[:8]}')
            db.execute('update orders set state=?,job=? where id=?',('submitted',job,oid))
            db.execute('insert or ignore into print_history values (?,?,?,?,?,?)',(oid,job,'submitted',sha,str(out),now))
            db.execute('update print_requests set state=?,job=? where request_id=?',('submitted',job,request_id))
            db.commit()
            log('submitted',job=job,pages=len(groups[0]['pages'])+1)
            return job
        except Exception:
            db.execute('update orders set state=? where id=?',('uncertain',oid))
            db.execute('update print_requests set state=? where request_id=?',('uncertain',request_id))
            db.commit(); log('submission_uncertain',source_hash=sha[:16])
            raise

def list_printers():
    """CUPS queues as [{name, default, label_4x6}]."""
    out=subprocess.run(['/usr/bin/lpstat','-p','-d'],capture_output=True,text=True,timeout=10).stdout
    default=next((line.split(':',1)[1].strip() for line in out.splitlines() if line.startswith('system default destination:')),None)
    names=[line.split()[1] for line in out.splitlines() if line.startswith('printer ')]
    printers=[]
    for name in names:
        opts=subprocess.run(['/usr/bin/lpoptions','-p',name,'-l'],capture_output=True,text=True,timeout=10).stdout
        printers.append(dict(name=name,default=name==default,label_4x6='w288h432' in opts))
    return printers

def printer_ready(name):
    return subprocess.run(['/usr/bin/lpstat','-p',name],capture_output=True,text=True,timeout=10).returncode==0

def job_status(job):
    # Read-only IPP query distinguishes a genuinely completed job from
    # cancelled/aborted jobs that also appear in lpstat's completed listing.
    number=int(job.rsplit('-',1)[1])
    def attr(tag,name,value):
        n=name.encode();v=value.encode()
        return bytes([tag])+struct.pack('!H',len(n))+n+struct.pack('!H',len(v))+v
    request=struct.pack('!BBHI',2,0,9,1)+b'\x01'
    request+=attr(0x47,'attributes-charset','utf-8')+attr(0x48,'attributes-natural-language','en')
    request+=attr(0x45,'job-uri',f'ipp://localhost/jobs/{number}')+b'\x03'
    class CupsConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            self.sock.settimeout(self.timeout)
            self.sock.connect('/private/var/run/cupsd')
    conn=CupsConnection('localhost',631,timeout=10)
    try:
        conn.request('POST',f'/jobs/{number}',request,{'Content-Type':'application/ipp'})
        response=conn.getresponse();data=response.read()
        if response.status!=200 or len(data)<8 or struct.unpack('!H',data[2:4])[0]>255:
            raise RuntimeError('CUPS job attributes unavailable')
    finally: conn.close()
    i=8;name='';result={}
    while i<len(data):
        tag=data[i];i+=1
        if tag==3: break
        if tag<16: continue
        n=struct.unpack('!H',data[i:i+2])[0];i+=2
        if n:name=data[i:i+n].decode()
        i+=n;length=struct.unpack('!H',data[i:i+2])[0];i+=2
        value=data[i:i+length];i+=length
        if name in {'job-state','job-impressions-completed','job-media-sheets-completed'}:
            result[name]=int.from_bytes(value,'big')
    return result
