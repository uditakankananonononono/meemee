export interface RetryPolicyOptions { maxAttempts?:number; backoffBaseMs?:number; backoffMultiplier?:number; backoffMaxMs?:number; retryStatuses?:Iterable<number>; retryMethods?:Iterable<string>; respectRetryAfter?:boolean; retryAfterMaxMs?:number; random?:()=>number }
export class RetryPolicy {
 readonly maxAttempts:number; private base:number;private multiplier:number;private max:number;private statuses:Set<number>;private methods:Set<string>;private respect:boolean;private afterMax:number;private random:()=>number;
 constructor(o:RetryPolicyOptions={}){this.maxAttempts=o.maxAttempts??3;this.base=o.backoffBaseMs??500;this.multiplier=o.backoffMultiplier??2;this.max=o.backoffMaxMs??30000;this.statuses=new Set(o.retryStatuses??[408,429,500,502,503,504]);this.methods=new Set(Array.from(o.retryMethods??["GET","HEAD","DELETE"],x=>x.toUpperCase()));this.respect=o.respectRetryAfter??true;this.afterMax=o.retryAfterMaxMs??120000;this.random=o.random??Math.random}
 canRetry(method:string,attempt:number,hasIdempotencyKey=false){return attempt<this.maxAttempts&&(this.methods.has(method.toUpperCase())||(method.toUpperCase()==="POST"&&hasIdempotencyKey))}
 isRetryableStatus(s:number){return this.statuses.has(s)}
 delay(attempt:number,retryAfter?:number){if(retryAfter!==undefined&&this.respect)return Math.max(0,Math.min(retryAfter*1000,this.afterMax));return this.random()*Math.min(this.base*this.multiplier**(attempt-1),this.max)}
}
