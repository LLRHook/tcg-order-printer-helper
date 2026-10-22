import glob, os, tempfile, tracemalloc, unittest
from pathlib import Path
from unittest import mock
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

    def test_order_id_cannot_be_a_prefix_of_a_longer_identifier(self):
        for suffix in ('X','1','-EXTRA','_EXTRA','é'):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(ValueError,'exactly one order'):
                    core.parse(self.write(slip(((ORDER_A+suffix,1),))))

    def test_malformed_order_field_cannot_hide_beside_a_valid_field(self):
        malformed=[ORDER_B+suffix for suffix in ('X','1','-EXTRA','_EXTRA','é','!')]
        malformed.extend(('',ORDER_B[:-1],'INVALID'))
        for invalid in malformed:
            for header,footer in ((invalid,ORDER_A),(ORDER_A,invalid)):
                with self.subTest(header=header,footer=footer):
                    with self.assertRaisesRegex(ValueError,'exactly one order'):
                        core.parse(self.write(slip(header_order=header,footer_order=footer)))

    def test_order_fields_must_agree_after_case_normalization(self):
        groups=core.parse(self.write(slip(((ORDER_A,2),),header_order=ORDER_A.lower())))
        self.assertEqual([(g['id'],g['pages']) for g in groups],[(ORDER_A,[0,1])])
        with self.assertRaisesRegex(ValueError,'exactly one order'):
            core.parse(self.write(slip(header_order=ORDER_B)))

    def test_invalid_page_count_does_not_allocate_declared_pages(self):
        source=self.write(slip(declared_total=100000))
        tracing=tracemalloc.is_tracing()
        if not tracing: tracemalloc.start()
        before=tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        try:
            with self.assertRaisesRegex(ValueError,'continuation page'):
                core.parse(source)
            _,peak=tracemalloc.get_traced_memory()
        finally:
            if not tracing: tracemalloc.stop()
        self.assertLess(peak-before,4*1024*1024,'Reject the count using the actual pages before allocating the claimed range')

    def test_missing_repeated_and_out_of_order_pages_are_rejected(self):
        source=PdfReader(self.write(slip(((ORDER_A,3),))))
        for indices in ((0,1),(0,1,1),(1,0,2),(0,2,1),(1,2)):
            with self.subTest(indices=indices):
                with core.PdfWriter() as writer:
                    for i in indices: writer.add_page(source.pages[i])
                    path=self.dir/'invalid.pdf'
                    with path.open('wb') as stream: writer.write(stream)
                with self.assertRaises(ValueError): core.parse(path)

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

    def test_build_fits_all_supported_address_lines(self):
        for lines in (
            ['Jane Buyer','Example Company','42 Test Ave','Apt 3','Springfield, IL 62701','United States'],
            ['Jane Buyer','Example Company','Department 2','42 Test Ave','Apt 3','Springfield, IL 62701','United States'],
        ):
            with self.subTest(line_count=len(lines)):
                source=self.write(slip(address=lines))
                groups=core.parse(source)
                self.assertEqual(groups[0]['address'],lines)
                out=core.build(source,self.dir/'out.pdf',groups,CONF)
                label=PdfReader(out).pages[1]
                text=label.extract_text()
                for line in lines: self.assertIn(line,text)
                positions=[]
                label.extract_text(visitor_text=lambda text,cm,tm,font,size:
                                   positions.append((tm[5],size)) if text.strip() in lines else None)
                self.assertEqual(len(positions),len(lines))
                self.assertTrue(all(y-size*1.25>=22 for y,size in positions))

    def test_build_failure_removes_partial_output_and_preserves_existing_pdf(self):
        source=self.write(slip())
        out=self.dir/'out.pdf'; out.write_bytes(b'existing label')
        def fail_write(stream):
            stream.write(b'partial label with synthetic buyer address')
            raise OSError('disk full')
        with mock.patch.object(core.PdfWriter,'write',side_effect=fail_write):
            with self.assertRaisesRegex(OSError,'disk full'):
                core.build(source,out,core.parse(source),CONF)
        self.assertEqual(out.read_bytes(),b'existing label')
        self.assertEqual(set(self.dir.iterdir()),{source,out})

    def test_build_output_is_private_while_being_written(self):
        source=self.write(slip())
        write=core.PdfWriter.write
        modes=[]
        def observe_write(writer,stream):
            modes.append(os.fstat(stream.fileno()).st_mode & 0o777)
            return write(writer,stream)
        for mask in (0o022,0o077,0o777):
            with self.subTest(umask=oct(mask)):
                modes.clear()
                previous_umask=os.umask(mask)
                try:
                    with mock.patch.object(core.PdfWriter,'write',autospec=True,side_effect=observe_write):
                        core.build(source,self.dir/'out.pdf',core.parse(source),CONF)
                finally:
                    os.umask(previous_umask)
                self.assertEqual(modes,[0o600])

    def test_build_rename_failure_cleans_up_and_preserves_existing_pdf(self):
        source=self.write(slip())
        out=self.dir/'out.pdf'; out.write_bytes(b'existing label')
        with mock.patch.object(core.os,'replace',side_effect=OSError('rename failed')):
            with self.assertRaisesRegex(OSError,'rename failed'):
                core.build(source,out,core.parse(source),CONF)
        self.assertEqual(out.read_bytes(),b'existing label')
        self.assertEqual(set(self.dir.iterdir()),{source,out})

    def test_build_does_not_overwrite_unrelated_temporary_file(self):
        source=self.write(slip())
        unrelated=self.dir/'out.tmp'; unrelated.write_bytes(b'keep this file')
        core.build(source,self.dir/'out.pdf',core.parse(source),CONF)
        self.assertEqual(unrelated.read_bytes(),b'keep this file')

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
