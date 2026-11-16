'use strict';
function budgetText(data) {
  if(data.personal)return (data.monetary_budget_enabled?'个人模型：已启用逐模型金额预算。':'个人模型：未启用金额预算，费用未知。')+(data.paid_calls_blocked?' 存在进行中或用量未知请求，暂停新调用。':'')+(data.held_unknown_calls?`\n${data.held_unknown_calls}条费用待核对，按上限预留 ¥${data.held_upper_cny}；仅限原金额预算与适用累计授权内继续。`:'')+'\n'+data.note;
  const lines = ['人民币账号额度（本项目估算，不是平台实时余额）'];
  for (const account of data.accounts || []) {
    const remaining = account.estimated_remaining_cny;
    lines.push(`${account.account_id}：${account.status === 'unknown' || remaining == null ? '余额未知，暂停付费' : `估算剩余 ¥${remaining}`} / 累计授权 ¥${account.authorized_cny}`);
    if (account.low_balance) lines.push(`⚠ 低余额提醒：已达到 ¥${account.warning_threshold_cny} 阈值，请核对平台控制台。`);
  }
  if (data.paid_calls_blocked) lines.push('费用尚未结清，已阻止所有账号的新付费请求。');
  if (!(data.accounts || []).length) lines.push('未登记人民币账号。');
  lines.push(data.note || '不包含其他应用的消费。');
  return lines.join('\n');
}
if (typeof module !== 'undefined') module.exports = {budgetText};
