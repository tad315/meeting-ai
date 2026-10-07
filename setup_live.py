"""Download only CPU inference files, directly to the chosen D: folder."""
import argparse, hashlib, json
from pathlib import Path
import httpx

repo='csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26'
names={'tokens':'tokens.txt', 'encoder':'encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx',
       'decoder':'decoder-epoch-99-avg-1-chunk-16-left-128.onnx',
       'joiner':'joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx'}

def main():
    global repo,names
    p=argparse.ArgumentParser();p.add_argument('--target',type=Path,default=Path(__file__).parent/'data/models/streaming-en-giga')
    p.add_argument('--variant',choices=['2023-06-26','2023-06-21'],default='2023-06-21')
    args=p.parse_args()
    if args.variant=='2023-06-21':
        repo=repo.replace('2023-06-26',args.variant)
        names={k:v.replace('-chunk-16-left-128','') for k,v in names.items()}
    target=args.target;target.mkdir(parents=True,exist_ok=True)
    with httpx.Client(follow_redirects=True,timeout=120) as client:
        meta=client.get('https://huggingface.co/api/models/'+repo);meta.raise_for_status()
        revision=meta.json()['sha'];files={}
        for role,name in names.items():
            path=target/name
            if not path.exists():
                print('Downloading '+name,flush=True)
                partial=path.with_suffix('.part')
                with client.stream('GET',f'https://huggingface.co/{repo}/resolve/{revision}/{name}') as r:
                    r.raise_for_status()
                    with partial.open('wb') as f:
                        for data in r.iter_bytes(1024*1024):f.write(data)
                partial.replace(path)
            with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
            files[role]={'name':name,'sha256':digest}
        (target/'manifest.json').write_text(json.dumps(dict(repo=repo,revision=revision,files=files),indent=2))
    print('Streaming model ready: '+str(target),flush=True)

if __name__=='__main__':main()
