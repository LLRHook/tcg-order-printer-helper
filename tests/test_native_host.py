import base64, io, json, struct, tempfile, unittest, uuid
from pathlib import Path
from unittest import mock
from tcg_order_printer_helper import core, native_host, wizard
from fixtures import slip, ORDER_A, ORDER_B

EXT='a'*32
ORIGIN=f'chrome-extension://{EXT}/'

def frames(*messages):
    out=b''
    for m in messages:
        raw=json.dumps(m).encode(); out+=struct.pack('=I',len(raw))+raw
    return io.BytesIO(out)

def replies(stream):
    data=stream.getvalue(); out=[]; i=0
    while i<len(data):
        n=struct.unpack('=I',data[i:i+4])[0]; out.append(json.loads(data[i+4:i+4+n])); i+=4+n
    return out

class HostTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.state=Path(self.tmp.name)
        core.save_config(self.state,{'printer':'Label','return_address':['My Shop','1 Main St','Townsville, VA 20000'],'extension_ids':[EXT]})
        patches=[mock.patch.object(core,'printer_ready',return_value=True),
                 mock.patch.object(core,'submit',return_value='Label-7'),
                 mock.patch.object(core,'job_status',return_value={'job-state':9,'job-media-sheets-completed':2})]
        self.mocks=[p.start() for p in patches]
        for p in patches: self.addCleanup(p.stop)
    def tearDown(self): self.tmp.cleanup()

    def serve(self,*messages,origin=ORIGIN):
        out=io.BytesIO(); code=native_host.serve(origin,frames(*messages),out,self.state)
        return code,replies(out)

    def print_message(self,data=None,order=ORDER_A,token=None):
        return {'command':'print','orderId':order,'requestId':token or str(uuid.uuid4()),
                'pdfBase64':base64.b64encode(data if data is not None else slip()).decode()}

    def test_unknown_origin_refused_without_reply(self):
        code,out=self.serve({'command':'ping'},origin='chrome-extension://'+'b'*32+'/')
        self.assertEqual((code,out),(2,[]))

    def test_ping_reports_printer(self):
        code,out=self.serve({'command':'ping'})
        self.assertEqual(code,0)
        self.assertEqual((out[0]['ok'],out[0]['printer']),(True,'Label'))

    def test_ping_before_setup(self):
        (self.state/'config.json').write_text(json.dumps({'extension_ids':[EXT]}))
        _,out=self.serve({'command':'ping'})
        self.assertEqual(out[0],{'ok':False,'error':'PRINTER_NOT_CONFIGURED'})

    def test_print_completes_and_replay_is_idempotent(self):
        message=self.print_message()
        _,out=self.serve(message,message)
        self.assertEqual((out[0]['state'],out[0]['job'],out[0]['pages']),('completed','Label-7',2))
        self.assertTrue(out[1]['reusedRequest'])
        self.assertEqual(self.mocks[1].call_count,1)

    def test_order_mismatch_rejected(self):
        _,out=self.serve(self.print_message(order=ORDER_B))
        self.assertEqual(out[0]['error'],'PDF_ORDER_MISMATCH')
        self.mocks[1].assert_not_called()

    def test_non_pdf_and_truncated_rejected(self):
        _,out=self.serve(self.print_message(b'hello'),self.print_message(slip(truncate=True)))
        self.assertEqual([o['error'] for o in out],['INVALID_OR_INCOMPLETE_PDF']*2)

    def test_unresolved_prior_job_blocks_new_click(self):
        self.mocks[2].return_value={'job-state':5}
        # Skip the 18 s completion wait: the first poll is already past the deadline.
        with mock.patch.object(native_host.time,'monotonic',side_effect=[0,100,200,300]):
            _,out=self.serve(self.print_message(),self.print_message())
        self.assertEqual(out[0]['state'],'submitted')
        self.assertEqual(out[1]['error'],'PREVIOUS_SUBMISSION_NEEDS_QUEUE_CHECK')

    def test_oversized_frame_closes_stream(self):
        out=io.BytesIO()
        code=native_host.serve(ORIGIN,io.BytesIO(struct.pack('=I',native_host.MAX_MESSAGE+1)),out,self.state)
        self.assertEqual((code,replies(out)[0]['error']),(1,'MESSAGE_TOO_LARGE'))

class WizardTest(unittest.TestCase):
    def test_non_interactive_setup_registers_installed_browsers(self):
        with tempfile.TemporaryDirectory() as tmp:
            support=Path(tmp)/'support'; (support/'Google/Chrome').mkdir(parents=True)
            state=Path(tmp)/'state'
            with mock.patch.object(wizard,'SUPPORT',support), \
                 mock.patch.object(core,'list_printers',return_value=[{'name':'Label','default':False,'label_4x6':True}]), \
                 mock.patch('builtins.print'):
                wizard.run(state,'Label',['Shop','1 Main St'],[EXT],interactive=False)
            manifest=json.loads((support/'Google/Chrome/NativeMessagingHosts'/f'{native_host.HOST_NAME}.json').read_text())
            self.assertEqual(manifest['path'],str(state/'bin'/'native-host'))
            self.assertEqual(set(manifest['allowed_origins']),{ORIGIN,f'chrome-extension://{wizard.DEV_EXTENSION_ID}/'})
            self.assertFalse((support/'Microsoft Edge').exists())
            self.assertTrue(native_host.origin_allowed(ORIGIN,state))

if __name__=='__main__': unittest.main()
