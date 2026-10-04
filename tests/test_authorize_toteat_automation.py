"""Handoff probado con ejecutables y respuestas ficticios: jamás abre el llavero."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts import authorize_toteat_automation as handoff


class AutomationHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'private').mkdir();(self.root/'native').mkdir()
        paths={'private/oveja-toteat-automation':b'FAKE-AUTO','private/oveja-toteat-diagnostics':b'FAKE-CURRENT','native/ToteatReader.swift':b'FAKE-BASE','private/ToteatAutomationReader.generated.swift':b'FAKE-GENERATED'}
        for name,content in paths.items():(self.root/name).write_bytes(content)
        digest=lambda name:hashlib.sha256(paths[name]).hexdigest()
        self.manifest={'version':1,'binary':'private/oveja-toteat-automation','binary_sha256':digest('private/oveja-toteat-automation'),'base_source_sha256':digest('native/ToteatReader.swift'),'generated_source_sha256':digest('private/ToteatAutomationReader.generated.swift')}
        (self.root/'private/toteat-automation-manifest.json').write_text(json.dumps(self.manifest))
        self.path=self.root/'private/toteat-sales-config.json'
        self.config={'version':1,'enabled':True,'binary':'private/oveja-toteat-diagnostics','binary_sha256':digest('private/oveja-toteat-diagnostics'),'scope':{'restaurant_id':'DEMO','local_id':'1'},'shift_start':'2026-10-02','start_after':'2026-10-04T17:00:00+00:00','test_order_ids':['FAKE'],'automatic_shift_lookup':False}
        self.path.write_text(json.dumps(self.config));self.before=self.path.read_bytes();self.calls=[]
        self.result={'ok':True,'persistent_access_verified':True,'token_exported':False}

    def runner(self,args,**kwargs):
        self.calls.append(args)
        result={'ok':True} if args[-1]=='self-test' else self.result
        return subprocess.CompletedProcess(args,0,stdout=json.dumps(result).encode())

    def run_main(self):
        with patch.object(handoff,'ROOT',self.root),patch.object(handoff.sys,'platform','darwin'),patch.object(handoff.sys.stdin,'isatty',return_value=True),patch.object(handoff.subprocess,'run',side_effect=self.runner),redirect_stdout(io.StringIO()):
            handoff.main()

    def test_preflight_runs_signature_and_self_test_only(self):
        with patch.object(handoff.subprocess,'run',side_effect=self.runner):
            handoff.preflight(self.root)
        self.assertEqual([args[-1] for args in self.calls],[str(self.root/self.manifest['binary']),'self-test'])
        self.assertEqual(self.path.read_bytes(),self.before)

    def test_success_switches_config_only_after_persistent_access(self):
        self.run_main();value=json.loads(self.path.read_text())
        self.assertEqual(value['binary'],self.manifest['binary']);self.assertTrue(value['automatic_shift_lookup'])
        self.assertEqual(value['scope'],self.config['scope']);self.assertEqual(value['test_order_ids'],['FAKE'])
        self.assertEqual([args[-1] for args in self.calls][1:],['self-test','authorize-version'])
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_denied_or_nonpersistent_access_preserves_current_config(self):
        for result in [{'ok':False},{'ok':True,'persistent_access_verified':False,'token_exported':False},{'ok':True,'persistent_access_verified':True,'token_exported':True},[]]:
            self.result=result
            with self.assertRaises(SystemExit):self.run_main()
            self.assertEqual(self.path.read_bytes(),self.before)

    def test_changed_binary_stops_before_any_authorization(self):
        (self.root/self.manifest['binary']).write_bytes(b'CHANGED')
        with self.assertRaises(SystemExit):self.run_main()
        self.assertEqual(self.calls,[]);self.assertEqual(self.path.read_bytes(),self.before)

    def test_invalid_config_stops_before_any_authorization(self):
        self.path.write_text(json.dumps(dict(self.config,scope=[])))
        with self.assertRaises(SystemExit):self.run_main()
        self.assertEqual(self.calls,[])

    def test_concurrent_configuration_change_is_not_overwritten(self):
        original=self.runner
        def changed(args,**kwargs):
            if args[-1]=='authorize-version':self.path.write_text(json.dumps(dict(self.config,enabled=False)))
            return original(args,**kwargs)
        self.runner=changed
        with self.assertRaisesRegex(SystemExit,'configuración cambió'):self.run_main()
        value=json.loads(self.path.read_text());self.assertFalse(value['enabled']);self.assertFalse(value['automatic_shift_lookup'])

    def test_noninteractive_handoff_never_requests_access(self):
        with patch.object(handoff.sys.stdin,'isatty',return_value=False),patch.object(handoff.subprocess,'run') as run:
            with self.assertRaises(SystemExit):handoff.main()
            run.assert_not_called()
