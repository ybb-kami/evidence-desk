"""Bound local reader computation; only one immutable PDF's outline and pages retained."""
from threading import Lock
from reader import build_reader
class ReaderCache:
    def __init__(self):
        self._id=None; self._reader=None; self._lock=Lock()
    def get(self,store,docid,mode='all',page=0):
        with self._lock:
            if self._id!=docid:
                rows=store.records([docid],archived=None)
                if not rows:return None
                result=build_reader(rows[0])
                self._id,self._reader=docid,result
            r=self._reader
            if mode=='page':
                if not 1<=page<=len(r['pages']):raise ValueError('页码超出范围。')
                return r['pages'][page-1]
            if mode=='outline' and len(r['pages'])>80:
                return {**r,'paged':True,'pages':[{'page':p['page'],'label':p['label']} for p in r['pages']]}
            return r
