"""Prepare a separate Caddyfile candidate; never overwrite/reload active config."""
from pathlib import Path
import argparse,hashlib,json,re

def patch_config(text):
    starts=list(re.finditer(r'(?m)^erp\.loxby\.cl[ \t]*\{[ \t]*$',text))
    if len(starts)!=1:raise ValueError('Expected exactly one ERP site block')
    start=starts[0].start();depth=0;end=None
    for line in text[start:].splitlines(keepends=True):
        content=line.split('#',1)[0]
        depth+=content.count('{')-content.count('}')
        if depth==0:
            end=start+len(line);break
        start+=len(line)
    start=starts[0].start()
    if end is None:raise ValueError('ERP site block is incomplete')
    site=text[start:end]
    if 'closure_v2_attachment' in site or 'closure_v2_exempt' in site:raise ValueError('V2 rules already present; inspect instead of applying twice')
    rule=re.compile(r'(?m)^([ \t]*)request_body @closure_attachment[ \t]*\{\s*\n[ \t]*max_size 3145728\s*\n[ \t]*\}')
    matches=list(rule.finditer(site))
    if len(matches)!=1:raise ValueError('Existing legacy upload rule differs')
    match=matches[0];indent=match.group(1)
    addition='\n'+ '\n'.join(indent+line if line else '' for line in [
        '# V2 photos: 3 MiB binary becomes 4 MiB base64 plus JSON metadata.',
        '@closure_v2_attachment {','    method POST',
        '    path_regexp closure_v2_file ^/api/cash-closures/v2/[a-f0-9]{32}/(?:attach|reference-attach)$',
        '}','request_body @closure_v2_attachment {','    max_size 5242880','}'])
    site=site[:match.end()]+addition+site[match.end():]
    exempt=re.compile(r'(?m)^([ \t]*)not[ \t]*\{\s*\n[ \t]*method POST\s*\n[ \t]*path_regexp closure_exempt ([^\n]+)\n[ \t]*\}')
    matches=list(exempt.finditer(site))
    if len(matches)!=1 or matches[0].group(2).strip()!='^/api/cash-closures/[a-f0-9]{32}/attach$':raise ValueError('Existing small-request exemption differs')
    match=matches[0];indent=match.group(1)
    addition='\n'+'\n'.join(indent+line for line in ['not {','    method POST',
        '    path_regexp closure_v2_exempt ^/api/cash-closures/v2/[a-f0-9]{32}/(?:attach|reference-attach)$','}'])
    site=site[:match.end()]+addition+site[match.end():]
    return text[:start]+site+text[end:]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('candidate',type=Path);args=parser.parse_args()
    if args.source.resolve()==args.candidate.resolve() or args.candidate.exists():raise SystemExit('Candidate must be a new, separate file')
    original=args.source.read_text();candidate=patch_config(original)
    with args.candidate.open('x') as handle:handle.write(candidate)
    args.candidate.chmod(0o600)
    print(json.dumps({'candidate':str(args.candidate),'source_sha256':hashlib.sha256(original.encode()).hexdigest(),'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),'active_configuration_changed':False,'service_reloaded':False}))

if __name__=='__main__':main()
