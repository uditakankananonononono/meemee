import { AuthenticationError, MeemeeError } from "./errors.js";
export interface AuthProvider { authorizationHeader(): string | Promise<string> }
export class TokenAuth implements AuthProvider {
  constructor(private readonly token: string) { if (!token.trim()) throw new TypeError("token must be a non-empty string"); }
  authorizationHeader(): string { return `Bearer ${this.token}`; }
}
export interface OIDCOptions { issuer?: string; tokenEndpoint?: string; clientId: string; clientSecret: string; scope?: string; leewaySeconds?: number; fetch?: typeof globalThis.fetch; now?: () => number }
export class OIDCClientCredentialsAuth implements AuthProvider {
  private endpoint?: string; private token?: string; private expiresAt=0; private pending?: Promise<void>;
  private readonly fetcher: typeof globalThis.fetch; private readonly now:()=>number; private readonly leeway:number;
  constructor(private readonly options: OIDCOptions) {
    if (!options.issuer && !options.tokenEndpoint) throw new TypeError("provide issuer or tokenEndpoint");
    if (!options.clientId || !options.clientSecret) throw new TypeError("clientId and clientSecret are required");
    this.endpoint=options.tokenEndpoint; this.fetcher=options.fetch ?? globalThis.fetch; this.now=options.now ?? Date.now; this.leeway=(options.leewaySeconds ?? 60)*1000;
  }
  refresh(): void { this.token=undefined; this.expiresAt=0; }
  async authorizationHeader(): Promise<string> { if (!this.token || this.now() >= this.expiresAt-this.leeway) await this.obtain(); return `Bearer ${this.token}`; }
  private async obtain(): Promise<void> { if (this.pending) return this.pending; this.pending=this.fetchToken().finally(()=>{this.pending=undefined}); return this.pending; }
  private async fetchToken(): Promise<void> {
    if (!this.endpoint) { const url=`${this.options.issuer!.replace(/\/$/,"")}/.well-known/openid-configuration`; let r:Response; try{r=await this.fetcher(url)}catch(e){throw new AuthenticationError(`OIDC discovery request failed: ${String(e)}`,0)} if(!r.ok)throw new AuthenticationError(`OIDC discovery failed with status ${r.status}`,r.status); const d=await r.json() as {token_endpoint?:unknown}; if(typeof d.token_endpoint!=="string")throw new AuthenticationError("OIDC discovery document has no token_endpoint",0); this.endpoint=d.token_endpoint; }
    const body=new URLSearchParams({grant_type:"client_credentials"}); if(this.options.scope)body.set("scope",this.options.scope);
    let r:Response; try { r=await this.fetcher(this.endpoint,{method:"POST",headers:{Authorization:`Basic ${base64(`${this.options.clientId}:${this.options.clientSecret}`)}`,"Content-Type":"application/x-www-form-urlencoded"},body}); } catch(e){throw new AuthenticationError(`OIDC token request failed: ${String(e)}`,0)}
    const data=await r.json().catch(()=>({})) as {access_token?:unknown;token_type?:unknown;expires_in?:unknown;error?:unknown;error_description?:unknown};
    if(!r.ok)throw new AuthenticationError(`OIDC token request rejected with status ${r.status}: ${String(data.error_description??data.error??"")}`,r.status);
    if(typeof data.access_token!=="string"||!data.access_token)throw new AuthenticationError("OIDC token endpoint response has no access_token",0);
    if(data.token_type!==undefined&&String(data.token_type).toLowerCase()!=="bearer")throw new AuthenticationError("OIDC token endpoint returned unsupported token_type",0);
    const lifetime=Number(data.expires_in??300); if(!Number.isFinite(lifetime)||lifetime<=0)throw new MeemeeError("OIDC token endpoint returned invalid expires_in"); this.token=data.access_token; this.expiresAt=this.now()+lifetime*1000;
  }
}
function base64(value:string):string { if(typeof globalThis.btoa==="function") return globalThis.btoa(value); throw new MeemeeError("Basic authentication encoding is unavailable in this runtime"); }
