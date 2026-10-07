const fs=require('fs'),vm=require('vm'),assert=require('assert');
let Worklet;const messages=[];
class Base{constructor(){this.port={postMessage:m=>messages.push(m)};}}
vm.runInNewContext(fs.readFileSync('web/step-one/live-worklet.js','utf8'),{AudioWorkletProcessor:Base,Int16Array,Math,registerProcessor:(_,c)=>Worklet=c});
const w=new Worklet();
for(let i=0;i<25;i++)w.process([[new Float32Array(128).fill(.5)],[new Float32Array(128).fill(-.25)]]);
assert.equal(messages.length,1);const pcm=new Int16Array(messages[0].packet);
assert.equal(pcm.length,6400);assert.equal(pcm[0],16384);assert.equal(pcm[1],-8192);
w.process([[new Float32Array(128).fill(.5)],[]]);w.port.onmessage({data:'stop'});
assert.equal(new Int16Array(messages[1].packet).length,256);assert.equal(messages[2].stopped,true);
assert.equal(w.process([]),false);console.log('Worklet: channel order, packet size, tail flush and stop OK');
