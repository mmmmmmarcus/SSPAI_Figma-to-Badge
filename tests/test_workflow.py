import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from PIL import Image
from badgegen.recipe import filename_stem, load_recipe
from badgegen.cli import build_many, blender_path
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('workflow', ROOT/'skills/figma-badge-3d/scripts/badge_workflow.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='badge 测试-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gen = self.root/'generator'
        (self.gen/'examples').mkdir(parents=True)
        (self.gen/'template.blend').write_bytes(b'fixture: validation only, not passed to Blender')
        shutil.copy(ROOT/'profiles.json', self.gen/'profiles.json')
        recipe=json.loads((ROOT/'examples/demo-silver.json').read_text())
        recipe['template']='../template.blend'
        (self.gen/'examples/demo-silver.json').write_text(json.dumps(recipe))
        (self.root/'badge.xml').write_text('<frame id="1:2" name="测试"><vector id="1:3"/></frame>',encoding='utf-8')
        (self.root/'context.xml').write_text('<section id="1:1"><frame id="1:2" x="0"/><frame id="1:4" name="测试_灰" x="0"/><frame id="1:5" name="Blender贴图" x="0" width="256" height="256"/></section>',encoding='utf-8')
        for name in ('color','gray','texture'):
            Image.new('RGBA',(256,256),(30,60,90,128)).save(self.root/(name+'.png'))
        shutil.copy(ROOT/'assets/demo/decoration.svg',self.root/'shape.svg')
        self.plan={'badge_url':'https://www.figma.com/design/EXAMPLE/Badge?node-id=1-2','profile':'demo-silver','badge_metadata':'badge.xml','context_metadata':'context.xml','mapping_reason':'Explicitly matched round disk with central decoration.', 'assets':{'border':{'node_id':'1:3','local_file':'shape.svg'},'texture':{'node_id':'1:5','local_file':'texture.png'}},'png_exports':{'color':{'node_id':'1:2','local_file':'color.png'},'gray':{'node_id':'1:4','local_file':'gray.png'}}}
    def prepare(self,gen=None):
        path=self.root/'plan.json'
        path.write_text(json.dumps(self.plan))
        return w.prepare(path,gen or self.gen)
    def test_prepare_unicode_names_and_evidence(self):
        result=self.prepare()
        recipe,_=load_recipe(result['recipe'])
        self.assertEqual(recipe['output_name'],'测试')
        self.assertEqual([p['name'] for p in recipe['png_exports']],['测试','测试_灰'])
        self.assertTrue((self.root/'prepared/provenance.json').is_file())
        self.assertEqual(Path(recipe['png_exports'][0]['image']).read_bytes(),(self.root/'color.png').read_bytes())
        with self.assertRaises(ValueError): self.prepare()
    def test_reject_wrong_texture_and_geometry(self):
        for role in ('texture','border'):
            old=self.plan['assets'][role]['node_id']
            self.plan['assets'][role]['node_id']='9:9'
            with self.assertRaises(ValueError): self.prepare()
            self.plan['assets'][role]['node_id']=old
        self.assertFalse((self.root/'prepared').exists())
    def test_reject_gray_mismatch(self):
        self.plan['png_exports']['gray']['node_id']='1:5'
        with self.assertRaises(ValueError): self.prepare()
    def test_unobserved_url_never_downloaded(self):
        (self.root/'response.txt').write_text('no asset')
        with self.assertRaisesRegex(ValueError,'not present'):
            w.asset_bytes({'url':'https://example.com/no.svg','source_response':'response.txt'},self.root)
    def test_profile_path_traversal_rejected(self):
        (self.gen/'profiles.json').write_text('{"../escape":{}}')
        with self.assertRaises(ValueError): self.prepare()
    def test_does_not_drop_extra_layers(self):
        p=self.gen/'examples/demo-silver.json'
        recipe=json.loads(p.read_text())
        recipe['layers'].append(dict(recipe['layers'][0],target='Other'))
        p.write_text(json.dumps(recipe))
        with self.assertRaisesRegex(ValueError,'exactly one'): self.prepare()
    def test_links_and_portable_names(self):
        self.assertEqual(w.parse_link('https://www.figma.com/design/main/branch/sub/Badge?node-id=1%3A2')['file_key'],'sub')
        for name in ('../x','CON','x?','x.','x:'):
            with self.assertRaises(ValueError): filename_stem(name)
        self.assertEqual(filename_stem('徽章 2026'),'徽章 2026')
    @unittest.skipUnless(os.environ.get('BADGEGEN_INTEGRATION'),'Enable Blender integration after setup')
    def test_real_export_delivery_cache_and_repair(self):
        result=self.prepare(ROOT)
        recipe=result['recipe']; out=self.root/'output'
        def build():
            r=build_many([recipe],out,blender_path())
            self.assertTrue(r['ok'],r)
            return r['results'][0]['status']
        self.assertEqual(build(),'built')
        dest=out/result['id']
        for ext in ('glb','usdz','png'): self.assertTrue((dest/('测试.'+ext)).exists())
        report=json.loads((dest/'report.json').read_text())
        for fmt in ('glb','usdz'):
            self.assertEqual(report['export_contents'][fmt],{'cameras':[],'lights':[]})
        self.assertEqual(build(),'cached')
        (dest/'测试_灰.png').write_bytes(b'corrupt')
        self.assertEqual(build(),'built')
        self.assertEqual((dest/'测试_灰.png').read_bytes(),(self.root/'gray.png').read_bytes())

if __name__=='__main__': unittest.main()
