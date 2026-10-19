from pathlib import Path
import importlib.util,unittest

path=Path(__file__).resolve().parents[1]/'scripts/prepare_cashier_photo_proxy.py'
spec=importlib.util.spec_from_file_location('photo_proxy_patch',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class ProxyPatchTests(unittest.TestCase):
    def baseline(self):
        # Keep the real legacy rules, remove only the proposed V2 additions.
        from test_cashier_photo_proxy import V2_BLOCK,V2_EXEMPT
        return (path.parents[1]/'deploy/Caddyfile.erp').read_text().replace(V2_BLOCK,'').replace(V2_EXEMPT,'').replace('erp.example.test {','erp.loxby.cl {')
    def test_preserves_other_sites_and_all_existing_directives(self):
        before='other.example.test {\n reverse_proxy 127.0.0.1:9000\n}\n'
        after='later.example.test {\n respond "preserve"\n}\n'
        text=before+self.baseline()+after;patched=module.patch_config(text)
        self.assertTrue(patched.startswith(before));self.assertTrue(patched.endswith(after))
        self.assertEqual(patched.count('max_size 5242880'),1)
        self.assertEqual(patched.count('/(?:attach|reference-attach)$'),2)
        self.assertIn('max_size 64KB',patched);self.assertIn('max_size 3145728',patched)
        # Every original line must still occur in its original order.
        iterator=iter(patched.splitlines())
        for line in text.splitlines():self.assertTrue(any(item==line for item in iterator),line)
    def test_refuses_duplicate_or_missing_erp_site(self):
        for text in [self.baseline()*2,'other.example.test {}']:
            with self.assertRaises(ValueError):module.patch_config(text)
    def test_refuses_existing_v2_rules(self):
        with self.assertRaises(ValueError):module.patch_config(module.patch_config(self.baseline()))
    def test_refuses_changed_legacy_limit(self):
        with self.assertRaises(ValueError):module.patch_config(self.baseline().replace('max_size 3145728','max_size 999'))

if __name__=='__main__':unittest.main()
