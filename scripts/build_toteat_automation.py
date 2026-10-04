"""Prepara un helper adicional con GET shiftstatus, sin leer el llavero."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    private = ROOT/'private'
    binary = private/'oveja-toteat-automation'
    manifest = private/'toteat-automation-manifest.json'
    if binary.exists() or manifest.exists():
        raise SystemExit('Ya existe una versión preparada; no se reemplaza automáticamente.')
    base = ROOT/'native/ToteatReader.swift'
    source = base.read_text()
    replacements = [
        ('else if mode == "tables" { endpoint = "tables"; parameters = [:] }',
         'else if mode == "tables" || mode == "shift-status" { endpoint = mode == "tables" ? "tables" : "shiftstatus"; parameters = [:] }'),
        ('case "fetch", "tables":','case "fetch", "tables", "shift-status":'),
        ('let tables = try request(demo, mode: "tables")',
         '''let shift = try request(demo, mode: "shift-status")
        let shiftQuery = URLComponents(url: shift.url!, resolvingAgainstBaseURL: false)!.queryItems!
        guard shift.httpMethod == "GET", shift.url?.host == "api.toteat.com", shift.url?.path == "/mw/or/1.0/shiftstatus",
              Set(shiftQuery.map { $0.name }) == Set(["xir", "xil", "xiu", "xapitoken"]) else { try fail("shift_self_test_failed") }
        let tables = try request(demo, mode: "tables")''')]
    for old,new in replacements:
        if source.count(old) != 1:
            raise SystemExit('El código base cambió; revisar antes de preparar el lector.')
        source = source.replace(old,new)
    generated = private/'ToteatAutomationReader.generated.swift'
    generated.write_text(source);generated.chmod(0o600)
    subprocess.run(['swiftc','-module-cache-path','/tmp/erp-oveja-swift-cache',str(generated),'-o',str(binary)],check=True)
    subprocess.run(['codesign','--force','--sign','-','--identifier','cl.oveja.erp.toteat.automation',str(binary)],check=True)
    subprocess.run(['codesign','--verify','--strict',str(binary)],check=True)
    check = subprocess.run([str(binary),'self-test'],capture_output=True,text=True,check=True)
    result = json.loads(check.stdout)
    if result.get('ok') is not True:
        raise SystemExit('Self-test rechazado.')
    value={'version':1,'binary':'private/oveja-toteat-automation',
           'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
           'base_source_sha256':hashlib.sha256(base.read_bytes()).hexdigest(),
           'generated_source_sha256':hashlib.sha256(generated.read_bytes()).hexdigest(),
           'prepared_only':True,'keychain_access_attempted':False,'additional_endpoint':'GET shiftstatus'}
    manifest.write_text(json.dumps(value,indent=2)+'\n');manifest.chmod(0o600)
    print('Lector de turnos preparado, firmado y self-test aprobado. Sin acceso al llavero ni consultas API.')


if __name__=='__main__':main()
