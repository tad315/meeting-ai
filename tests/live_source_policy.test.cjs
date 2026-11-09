const fs=require('fs'),assert=require('assert');
(async()=>{
 const source=fs.readFileSync('web/step-one/live-source-policy.js','utf8');
 const {sourceSelection}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
 assert.deepStrictEqual(sourceSelection('tab'),[false,true]);
 assert.deepStrictEqual(sourceSelection('mic'),[true,false]);
 assert.deepStrictEqual(sourceSelection('both'),[true,true]);
 assert.throws(()=>sourceSelection('invalid'));
 const html=fs.readFileSync('web/step-one/live.html','utf8');
 assert(html.includes('<option value="tab" selected>'));
 assert(!html.includes('id="mic" type="checkbox" checked'));
 console.log('Explicit audio selection: tab default, mic-only, intentional combined, invalid rejected OK');
})().catch(error=>{console.error(error);process.exitCode=1});
