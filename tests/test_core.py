import glob, os, tempfile, unittest
from pathlib import Path
from pypdf import PdfReader
from tcg_order_printer_helper import core
from fixtures import slip, ORDER_A, ORDER_B, ADDRESS

CONF={'printer':'Label','return_address':['My Shop','1 Main St','Townsville, VA 20000'],
      'lp_options':core.DEFAULT_LP_OPTIONS,'retention_days':7}

class ParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.dir=Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()
    def write(self,data,name='slip.pdf'):
        path=self.dir/name; path.write_bytes(data); return path

    def test_single_order_address(self):
        groups=core.parse(self.write(slip()))
        self.assertEqual([g['id'] for g in groups],[ORDER_A])
        self.assertEqual(groups[0]['address'],ADDRESS)

    def test_multi_page_and_multi_order(self):
        groups=core.parse(self.write(slip(((ORDER_A,2),(ORDER_B,1)))))
        self.assertEqual([(g['id'],g['pages']) for g in groups],[(ORDER_A,[0,1]),(ORDER_B,[2])])

    def test_incomplete_download_rejected(self):
        with self.assertRaisesRegex(ValueError,'incomplete'):
            core.parse(self.write(slip(truncate=True)))

    def test_address_without_city_line_rejected(self):
        with self.assertRaisesRegex(ValueError,'city'):
            core.parse(self.write(slip(address=['Jane Buyer','42 Test Ave','Nowhere'])))

    def test_build_adds_one_label_per_order_at_4x6(self):
        source=self.write(slip(((ORDER_A,2),(ORDER_B,1))))
        out=core.build(source,self.dir/'out.pdf',core.parse(source),CONF)
        pages=PdfReader(out).pages
        self.assertEqual(len(pages),5)
        self.assertTrue(all((float(p.mediabox.width),float(p.mediabox.height))==(288,432) for p in pages))
        self.assertIn('Order '+ORDER_A,pages[2].extract_text())
        self.assertEqual(oct(os.stat(out).st_mode & 0o777),'0o600')

    def test_prune_removes_only_old_pdfs(self):
        (self.dir/'inputs').mkdir(); old=self.dir/'inputs'/'old.pdf'; new=self.dir/'inputs'/'new.pdf'
        old.write_bytes(b'x'); new.write_bytes(b'x'); os.utime(old,(0,0))
        core.prune(self.dir,7)
        self.assertFalse(old.exists()); self.assertTrue(new.exists())

    def test_config_requires_printer_and_address(self):
        with self.assertRaises(core.SetupRequired): core.load_config(self.dir)
        with self.assertRaises(core.SetupRequired): core.save_config(self.dir,{**CONF,'return_address':['one']})
        core.save_config(self.dir,CONF)
        self.assertEqual(core.load_config(self.dir)['printer'],'Label')

@unittest.skipUnless(os.environ.get('TCG_SAMPLES'),'set TCG_SAMPLES to a folder of real packing slips')
class RealSamplesTest(unittest.TestCase):
    """Regression over real exports. Real slips hold buyer PII: never commit them."""
    def test_every_sample_parses_and_builds(self):
        paths=sorted(glob.glob(os.path.join(os.environ['TCG_SAMPLES'],'TCGplayer_PackingSlips_*.pdf')))
        self.assertTrue(paths)
        with tempfile.TemporaryDirectory() as tmp:
            for path in paths:
                with self.subTest(path=Path(path).name):
                    groups=core.parse(path)
                    out=core.build(path,Path(tmp)/'out.pdf',groups,CONF)
                    self.assertEqual(len(PdfReader(out).pages),sum(len(g['pages'])+1 for g in groups))

if __name__=='__main__': unittest.main()
