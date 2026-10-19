"""Resource guards and persisted failed-reading recovery, fictional data only."""
import json,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from erp import document_intake as di
from erp.local_invoice_reader import bounded_image,limit_resources,read_strips

class OCRResourceTests(unittest.TestCase):
    def test_concurrent_extraction_does_not_launch_second_decoder_and_releases_slot(self):
        entered=threading.Event();release=threading.Event();result=[]
        def slow(*args):entered.set();release.wait(3);return 'Folio: 1','extracted','Ficticio'
        with patch.object(di,'_extract',side_effect=slow) as reader:
            thread=threading.Thread(target=lambda:result.append(di.extract(b'fixture','image/png')));thread.start()
            self.assertTrue(entered.wait(2))
            try:self.assertEqual(di.extract(b'fixture2','image/png')[1],'busy');self.assertEqual(reader.call_count,1)
            finally:release.set();thread.join()
        with patch.object(di,'_extract',return_value=('','empty','Ficticio')):self.assertEqual(di.extract(b'fixture','image/png')[1],'empty')

    def test_structured_empty_text_is_not_falsely_reported_as_extracted(self):
        def empty(command,stdout,**kwargs):stdout.write(json.dumps({'reader_version':'fixture','text':'','raw_text':''}).encode());return SimpleNamespace(returncode=0)
        with patch.object(di,'advanced_available',return_value=True),patch.object(di.subprocess,'run',side_effect=empty):
            text,status,note=di.extract(b'fixture','image/png')
        self.assertEqual(status,'empty');self.assertIn('No se detectó texto',note)

    def test_child_failure_preserves_review_path_and_releases_slot(self):
        with patch.object(di,'advanced_available',return_value=True),patch.object(di.subprocess,'run',return_value=SimpleNamespace(returncode=-9)):
            for _ in range(2):
                text,status,note=di.extract(b'fixture','image/png');self.assertEqual((text,status),('','failed'));self.assertIn('original se conserva',note)

    def test_large_image_is_bounded_and_exif_orientation_is_applied_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'rotada.jpg';exif=Image.Exif();exif[274]=6
            Image.new('RGB',(3600,2400),'white').save(path,exif=exif)
            with bounded_image(path) as image:
                self.assertLessEqual(max(image.size),2000);self.assertLess(image.width,image.height);self.assertEqual(image.mode,'RGB')
            path=Path(folder)/'transparente.png';Image.new('RGBA',(40,30),(0,0,0,0)).save(path)
            with bounded_image(path) as image:self.assertEqual(image.getpixel((0,0)),(255,255,255))

    def test_linux_limit_does_not_raise_an_existing_tighter_limit(self):
        import resource
        with patch('erp.local_invoice_reader.sys.platform','linux'),patch.object(resource,'getrlimit',return_value=(128*1024*1024,128*1024*1024)),patch.object(resource,'setrlimit') as apply:
            limit_resources();apply.assert_called_once_with(resource.RLIMIT_DATA,(128*1024*1024,128*1024*1024))

    def test_strip_overlap_preserves_each_complete_text_line_once(self):
        class ImageRows:
            shape=(1000,1200,3)
            def __getitem__(self,key):return key.start,key.stop
        centers=[15,151,320,500,681,900,985];calls=[]
        def reader(slice):
            top,bottom=slice;calls.append((top,bottom));found=[]
            for y in centers:
                if top<=y-10 and y+10<=bottom:found.append([[[10,y-10-top],[1100,y-10-top],[1100,y+10-top],[10,y+10-top]],str(y),.99])
            return found,0
        boxes=read_strips(reader,ImageRows())
        self.assertCountEqual([text for box,text,score in boxes],[str(y) for y in centers])
        self.assertTrue(all(bottom-top<=384 for top,bottom in calls))
