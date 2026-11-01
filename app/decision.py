from __future__ import annotations
from typing import Any
class DecisionEngine:
    def __init__(self,rules:dict[str,Any]): self.rules=rules; self.required=tuple(rules.get('preconditions',{}).get('required_context_fields',[]))
    def decide(self,context:dict[str,Any],*,run_id:str,code_version:str,evidence_ids:list[str],decision_id:str)->dict[str,Any]:
        missing=[x for x in self.required if x not in context]
        if missing: return {'decision_id':decision_id,'run_id':run_id,'code_version':code_version,'evidence_ids':evidence_ids,'failure_class':'configuration_error','action':'pause','target_agent':None,'reason':'缺少上下文: '+', '.join(missing),'retries_left':context.get('retries_left'),'rule_id':None}
        for rule in self.rules.get('rules',[]):
            when=rule.get('when',{})
            if all(context.get(k)==v for k,v in when.items()): return {'decision_id':decision_id,'run_id':run_id,'code_version':code_version,'evidence_ids':evidence_ids,'failure_class':context.get('failure_class','unknown'),'action':rule.get('action','pause'),'target_agent':rule.get('target_agent'),'reason':rule.get('reason',''),'retries_left':context.get('retries_left'),'rule_id':rule.get('rule_id')}
        unmatched=self.rules.get('unmatched',{})
        return {'decision_id':decision_id,'run_id':run_id,'code_version':code_version,'evidence_ids':evidence_ids,'failure_class':context.get('failure_class','unknown'),'action':unmatched.get('action','pause'),'target_agent':None,'reason':unmatched.get('reason','状态未匹配，暂停核查'),'retries_left':context.get('retries_left'),'rule_id':None}
