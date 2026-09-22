import { h, toast, jsonBlock } from "../dom.js";
import * as api from "../api.js";
import { reportError } from "../app.js";

export async function renderAccounts(root) {
  const principal=h("input",{class:"input",placeholder:"Principal ID",maxlength:200});
  const plan=h("select",{class:"input"},...['starter','team','business'].map(value=>h("option",{value},value)));
  const quota=h("input",{class:"input",type:"number",min:1,max:1000000,placeholder:"Daily jobs"});
  const result=h("div");
  const requirePrincipal=()=>{const value=principal.value.trim();if(!value)toast("Principal ID is required.","warn");return value;};
  const assign=async()=>{const id=requirePrincipal();if(!id)return;if(!window.confirm(`Assign ${plan.value} to ${id}? This changes enforced limits.`))return;
    try{const value=await api.assignPrincipalPlan(id,plan.value);result.replaceChildren(jsonBlock(value));toast("Plan assigned.","ok");}catch(error){reportError(error,"plan assignment failed");}};
  const setQuota=async()=>{const id=requirePrincipal(),value=Number(quota.value);if(!id)return;if(!Number.isInteger(value)||value<1||value>1000000){toast("Daily jobs must be an integer from 1 to 1000000.","warn");return;}
    if(!window.confirm(`Set ${id} daily-job quota to ${value}?`))return;
    try{const response=await api.setPrincipalQuota(id,value);result.replaceChildren(jsonBlock(response));toast("Quota updated.","ok");}catch(error){reportError(error,"quota update failed");}};
  root.append(h("section",{class:"card"},h("h2",null,"Account administration"),
    h("p",{class:"muted"},"Administer Meemee principals after they are created by your identity provider. Identity creation, password policy and deletion remain at the IdP."),
    h("label",{class:"field-label"},"Principal ID"),principal,
    h("label",{class:"field-label"},"Commercial plan"),plan,h("button",{class:"button",type:"button",onclick:assign},"Assign plan"),
    h("label",{class:"field-label"},"Daily job quota override"),quota,h("button",{class:"button",type:"button",onclick:setQuota},"Set quota")),
    h("section",{class:"card"},h("h2",null,"Last change"),result));
}
