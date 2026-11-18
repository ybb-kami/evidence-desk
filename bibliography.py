"""Export only document-local identities; do not invent missing bibliographic fields."""
import re
def export_refs(docs):
    entries=[]; skipped=[]
    for doc in docs:
        identity=doc.get('bibliographic_identity') or {};title=identity.get('title')
        arx=doc.get('arxiv') or {};aid=arx.get('id');ver=arx.get('version')
        doi=identity.get('doi')
        arxok=isinstance(aid,str) and re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})',aid)
        doiok=isinstance(doi,str) and re.fullmatch(r'10\.\d{4,9}/[-._;()/:a-z0-9]+',doi,re.I)
        if not isinstance(title,str) or not title.strip() or not (arxok or doiok):
            skipped.append({'id':doc['id'],'name':doc['name'],'reason':'未同时识别首页题名及可定位标识；不生成猜测书目。'});continue
        entry={'id':'local-'+doc['id'][:16],'type':'document','title':title.strip()[:300]}
        if arxok:
            suffix=f'v{ver}' if type(ver) is int and ver>0 else ''
            entry['URL']='https://arxiv.org/abs/'+aid+suffix
            entry['archive']='arXiv';entry['archive_location']=aid+suffix
        if doiok:
            entry['DOI']=doi
            if 'URL' not in entry:entry['URL']='https://doi.org/'+doi
        entries.append({'csl':entry,'local_id':doc['id'],'filename':doc['name'],'source_page':1})
    return {'entries':entries,'skipped':skipped,'note':'仅从首页已识别字段生成 CSL-JSON 草稿，未在线核验；缺少作者、年份或刊名时不补猜，不用于声明完整正式引文。'}
