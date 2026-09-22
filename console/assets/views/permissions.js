import { h, clear, toast, fullTime, badge, jsonBlock } from "../dom.js";
import * as api from "../api.js";
import { reportError } from "../app.js";

export async function renderPermissions(root) {
  const principal=h("input",{class:"input",placeholder:"Principal ID",maxlength:200});
  const tool=h("input",{class:"input",placeholder:"Exact tool name, e.g. github.push_branch",maxlength:200});
  const expiry=h("input",{class:"input",type:"datetime-local"});
  const constraints=h("textarea",{class:"input",rows:5,placeholder:'Optional JSON constraints, e.g. {"owner":"acme","repository":"safe"}'});
  const registry=h("div");

  const load=async()=>{
    const id=principal.value.trim(); clear(registry);
    if(!id){registry.append(h("p",{class:"muted"},"Enter a principal ID.")); return;}
    try {
      const {approvals}=await api.listApprovals(id);
      if(!approvals.length){registry.append(h("p",{class:"muted"},"No grants for this principal.")); return;}
      registry.append(h("div",{class:"scroll-x"},h("table",{class:"table"},
        h("thead",null,h("tr",null,h("th",null,"Tool"),h("th",null,"Constraints"),h("th",null,"Expiry"),h("th",null,"State"),h("th",null,""))),
        h("tbody",null,approvals.map(item=>h("tr",null,
          h("td",null,h("code",null,item.tool)),h("td",null,item.argument_constraints?jsonBlock(item.argument_constraints):"all arguments"),
          h("td",{class:"muted"},item.expires_at?fullTime(item.expires_at):"never"),
          h("td",null,item.revoked_at?badge("revoked","warn"):badge("active","ok")),
          h("td",null,item.revoked_at?null:h("button",{class:"button button-danger",type:"button",onclick:async()=>{
            if(!window.confirm(`Revoke ${item.tool} for ${id}?`))return;
            try{await api.revokeApproval(id,item.tool);toast("Approval revoked.","ok");await load();}catch(error){reportError(error,"revoke failed");}
          }},"Revoke"))))))));
    } catch(error){reportError(error,"could not list approvals");}
  };
  const grant=async()=>{
    const id=principal.value.trim(), name=tool.value.trim();
    if(!id||!name){toast("Principal and exact tool name are required.","warn");return;}
    let parsed=null;
    if(constraints.value.trim()) { try { parsed=JSON.parse(constraints.value); if(!parsed||Array.isArray(parsed)||typeof parsed!=="object")throw new Error(); }
      catch {toast("Constraints must be a JSON object.","warn");return;} }
    const expiresAt=expiry.value?new Date(expiry.value).toISOString():null;
    if(!window.confirm(`Grant ${name} to ${id}${parsed?" with the displayed constraints":" for all arguments"}?`))return;
    try{await api.grantApproval(id,name,expiresAt,parsed);toast("Approval granted.","ok");await load();}
    catch(error){reportError(error,"grant failed");}
  };
  root.append(h("section",{class:"card"},h("h2",null,"Persistent permissions"),
    h("p",{class:"muted"},"Admin-only grants. Constraints are exact argument matches; omit them only when every call to the tool should be permitted."),
    h("label",{class:"field-label"},"Principal"),principal,
    h("label",{class:"field-label"},"Tool"),tool,
    h("label",{class:"field-label"},"Argument constraints (optional JSON object)"),constraints,
    h("label",{class:"field-label"},"Expires at (optional)"),expiry,
    h("div",{class:"row"},h("button",{class:"button",type:"button",onclick:grant},"Grant"),h("button",{class:"button button-quiet",type:"button",onclick:load},"Load grants"))),
    h("section",{class:"card"},h("h2",null,"Grant registry"),registry));
}
