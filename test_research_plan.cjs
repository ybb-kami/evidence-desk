const assert=require('node:assert/strict'),{build}=require('./research_plan.js');
const f={question:'比较两种检索方法',material:'公开论文',method:'BM25',baseline:'词频',measure:'召回率@5'};
assert.throws(()=>build({...f,baseline:' '}));const r=build(f);
for(const s of Object.values(f))assert.ok(r.markdown.includes(s));assert.ok(r.markdown.includes('尚无经核实的结果'));assert.ok(r.diagram.includes('方法：BM25'));assert.ok(r.diagram.includes('B --> E'));
const hostile=build({...f,method:'x"]\nclick X "javascript:alert(1)"\n%%'});assert.equal(hostile.diagram.split('\n').filter(x=>x.startsWith('click')).length,0);assert.ok(!hostile.diagram.includes('javascript:'));console.log('Research plan and user-defined comparison diagram tests passed');
