// Selecting both sources is explicit, never an accidental pair of defaults.
export function sourceSelection(mode){
  if(mode==='tab')return [false,true];
  if(mode==='mic')return [true,false];
  if(mode==='both')return [true,true];
  throw new Error('Choose an audio source.');
}
