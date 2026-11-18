"""Check a contestant-supplied official pdf/ locally. No downloads or uploads."""
import argparse, hashlib, json
from pathlib import Path
import fitz
NAMES=('1706.03762v7.pdf','1706.03762v1.pdf','1706.03762v1_img.pdf','UnderstandingDeepLearning_02_09_26_C.pdf')
def manifest(folder):
    result=[]
    for name in NAMES:
        file=folder/name
        if not file.is_file():
            result.append({'file':name,'status':'missing'});continue
        sha=hashlib.sha256()
        with file.open('rb') as stream:
            for data in iter(lambda:stream.read(1<<20),b''):sha.update(data)
        row={'file':name,'bytes':file.stat().st_size,'sha256':sha.hexdigest()}
        try:
            with fitz.open(file) as d:
                row['pages']=len(d)
                row['sampled_text_pages']=[i+1 for i in sorted({0,len(d)//2,len(d)-1}) if d[i].get_text().strip()]
                row['status']='present'
                if name==NAMES[3] and len(d)!=541:row['status']='unexpected_page_count'
                elif name==NAMES[2] and row['sampled_text_pages']:row['status']='scan_has_sampled_text'
        except (ValueError,RuntimeError,IndexError):row['status']='unreadable'
        result.append(row)
    return {'items':result,'source_verified':False,'deployment_validated':False,
            'note':'文件名、哈希和页数只描述本地输入；不能证明组委会来源，也不等于已通过部署验收。'}
if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('pdf_directory',type=Path)
    report=manifest(ap.parse_args().pdf_directory);print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(int(any(x['status']!='present' for x in report['items'])))
