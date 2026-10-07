class Capture extends AudioWorkletProcessor {
  constructor(){super();this.buffer=new Int16Array(6400);this.offset=0;this.running=true;
    this.port.onmessage=e=>{if(e.data==='stop'){this.running=false;this.flush();this.port.postMessage({stopped:true});}};}
  flush(){if(this.offset){const packet=this.buffer.slice(0,this.offset);this.port.postMessage({packet:packet.buffer},[packet.buffer]);this.offset=0;}}
  process(inputs){
    if(!this.running)return false;
    const count=inputs[0]?.[0]?.length||inputs[1]?.[0]?.length||128;
    for(let n=0;n<count;n++){
      for(let c=0;c<2;c++){const v=inputs[c]?.[0]?.[n]||0;this.buffer[this.offset++]=Math.round(Math.max(-1,Math.min(1,v))*32767);}
      if(this.offset===this.buffer.length)this.flush();
    }
    return true;
  }
}
registerProcessor('live-capture',Capture);
