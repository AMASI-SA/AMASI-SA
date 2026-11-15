/* No customer, merchant or production data. Every identity and amount is synthetic. */
const scope = 'a'.repeat(64), startedAt = '2026-11-01T00:00:00Z';
const sourceTime = '2026-11-15T08:00:00Z', evidenceTime = '2026-11-15T10:00:00Z';
const snapshot = {session_scope:scope,active:true,revision:7,started_at:startedAt,as_of:sourceTime,permissions:{upload:true,confirm:true}};
const statement = {id:'synthetic-weekly-statement',provider:'tabby',statement_reference:'SYNTHETIC-WEEK-01',filename:'weekly-tabby.xlsx',
  file_hash:'f'.repeat(64),native_currency:'SAR',row_count:3,uploaded_at:evidenceTime};
const counts = {matched:1,missing:1,different:0,blocked:1,confirmed:0};
const average = {available:true,provider:'tabby',native_currency:'SAR',calculation:'sale_variable_fees_before_vat_over_sale_principal',
  rate_percent:'7.47000000',principal_total:'100.00',fee_variable_net_total:'7.47',fee_fixed_net_total:'1.00',fixed_amount_per_sale:'1.00000000',
  fee_net_total:'8.47',fee_vat_total:'1.27',fee_total:'9.74',eligible_sale_rows:1,vat_rate:'0.15',vat_rate_percent:'15.00000000',
  vat_rate_status:'explicit_uniform',full_estimate_requires_vat:false,statement_id:statement.id,statement_reference:statement.statement_reference,
  source_as_of:'2026-11-11T00:00:00Z',source_date_basis:'sale_event_date',accepted_at:evidenceTime};
function detail(confirmed=false) {
  return {...snapshot,revision:confirmed?8:7,statement:{...statement},preview_hash:(confirmed?'c':'b').repeat(64),
    statement_evidence_as_of:evidenceTime,reviewed_at:evidenceTime,statement_average:{...average},counts:{...counts,missing:confirmed?0:1,confirmed:confirmed?1:0},bank_posting_allowed:false,
    rows:[
      {id:'sale-matched',order_number:'900001',provider:'tabby',kind:'capture',native_currency:'SAR',principal_amount:'100.00',fee_variable_net:'7.47',fee_fixed_net:'1.00',fee_net:'8.47',fee_vat:'1.27',fee_total:'9.74',
        event_date:'2026-11-11T00:00:00Z',plan_id:'Installments: 11 Months',payment_method:'tabby',source_row:7,installments:null,
        status:'matched',message:'دفع العميل مسجل؛ تُستخدم رسوم الكشف الفعلية دون تكرار الدفعة.',reason_code:null,
        existing:[{id:'synthetic-employee-sale',kind:'capture',amount:'100.00',reference:'SYNTHETIC-PAYMENT',financial_effect:'already_recorded'}],can_confirm:false,allowed_actions:[]},
      {id:'refund-missing',order_number:'900002',provider:'tabby',kind:'refund',native_currency:'SAR',principal_amount:'25.00',fee_net:'-1.98',fee_vat:'-0.30',fee_total:'-2.28',
        event_date:'2026-11-12T00:00:00Z',plan_id:'Installments: 5 Months',payment_method:'tabby',source_row:8,installments:null,
        status:confirmed?'already_confirmed':'missing',message:confirmed?'سُجل رد المبلغ مرة واحدة.':'هذا الرد مثبت في الكشف، ولم يُسجل ضمن ذمة المنصة بعد.',reason_code:null,existing:[],
        can_confirm:!confirmed,allowed_actions:confirmed?[]:['record_missing_refund'],
        proposed_effect:confirmed?undefined:{kind:'provider_refund',principal_amount:'25.00',native_currency:'SAR',provider:'tabby',remaining_before:'100.00',remaining_after:'75.00',bank_posting_allowed:false}},
      {id:'incomplete',order_number:'900003',provider:'tabby',kind:'payout_fee',native_currency:'SAR',principal_amount:null,fee_net:null,fee_vat:null,fee_total:null,
        event_date:null,plan_id:null,payment_method:null,source_row:9,installments:null,status:'blocked',message:'بيانات هذا السطر تحتاج مراجعة.',reason_code:'statement_amount_missing',
        existing:[],can_confirm:false,allowed_actions:[],action_url:'/operational-balances',action_label:'مراجعة الدفع والأرصدة'},
    ]};
}
function history(uploaded=false, confirmed=false) {
  return {...snapshot,revision:confirmed?8:7,items:uploaded?[{...statement,counts:detail(confirmed).counts,reviewed_at:evidenceTime,review_as_of:sourceTime,statement_average:{...average},status:'needs_review'}]:[],has_more:false};
}
module.exports = {scope,startedAt,detail,history};
