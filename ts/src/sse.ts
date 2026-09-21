export interface SSEMessage { kind:"event"|"comment"; event?:string; data?:string; id?:string; comment?:string }
export class SSEParser {
  private buffer=""; private decoder=new TextDecoder(); private data:string[]=[]; private eventType=""; private lastId?:string;
  get lastEventId():string|undefined{return this.lastId}
  feed(chunk:Uint8Array|string):SSEMessage[]{ this.buffer+=typeof chunk==="string"?chunk:this.decoder.decode(chunk,{stream:true}); const out:SSEMessage[]=[]; for(;;){const n=this.buffer.indexOf("\n");if(n<0)break;let line=this.buffer.slice(0,n);this.buffer=this.buffer.slice(n+1);if(line.endsWith("\r"))line=line.slice(0,-1);const m=this.line(line);if(m)out.push(m)}return out; }
  private line(line:string):SSEMessage|undefined { if(line==="")return this.dispatch();if(line.startsWith(":"))return {kind:"comment",comment:line.slice(1).replace(/^ /,"")};let field:string,value:string;const i=line.indexOf(":");if(i<0){field=line;value=""}else{field=line.slice(0,i);value=line.slice(i+1).replace(/^ /,"")}if(field==="data")this.data.push(value);else if(field==="event")this.eventType=value;else if(field==="id"&&!value.includes("\0"))this.lastId=value;return undefined; }
  private dispatch():SSEMessage|undefined { if(!this.data.length){this.eventType="";return undefined}const m:SSEMessage={kind:"event",event:this.eventType||"message",data:this.data.join("\n")};if(this.lastId!==undefined)m.id=this.lastId;this.data=[];this.eventType="";return m; }
}
