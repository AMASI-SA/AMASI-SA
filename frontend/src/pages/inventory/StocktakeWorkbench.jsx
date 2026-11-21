import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowClockwise,CheckCircle,ClipboardText,ClockCounterClockwise,MagnifyingGlass,Plus,ShieldCheck,WarningCircle} from '@phosphor-icons/react';
import {InventoryPageHeader,InventorySheet,InventorySurface} from '../../components/inventory/suite/InventorySuiteFrame';
import {
  loadInventoryStocktakeContext,listInventoryStocktakes,getInventoryStocktake,
  createInventoryStocktake,saveInventoryStocktake,actOnInventoryStocktake,
  postInventoryStocktake,newStocktakeRequestId,inventoryStocktakeMessage,
} from '../../services/inventoryStocktakes';
import './stocktakeWorkbench.css';

const SCHEMA='g47-stocktake-v1';
const STATUS={draft:'مسودة',returned:'معاد للتعديل',submitted:'بانتظار المراجعة',approved:'معتمد',rejected:'مرفوض',posted:'مرحّل'};
const ACTION={submit:'إرسال للمراجعة',approve:'اعتماد الجرد',return:'إعادة للتعديل',reject:'رفض الجرد',refresh_snapshot:'تحديث المقارنة',reload:'قراءة النسخة المحفوظة',post:'ترحيل الفروق',discard:'إغلاق دون حفظ'};
const UNIT={piece:'حبة',pieces:'حبة',gram:'غرام',kilogram:'كجم',meter:'متر',milliliter:'مل',liter:'لتر'};
const display=value=>value===null||value===undefined||value===''?'غير مثبت':String(value);
const unitName=value=>UNIT[value]||value||'غير مثبتة';
const editableStatus=status=>['draft','returned'].includes(status);
const isDenied=error=>[401,403].includes(error?.response?.status);
const uncertain=error=>!error?.response||error.response.status>=500||error.response.status===408;

export function normalizeStocktakeQuantity(value){
  return String(value??'').trim().replace(/[٠-٩]/g,d=>String(d.charCodeAt(0)-1632)).replace(/[۰-۹]/g,d=>String(d.charCodeAt(0)-1776)).replace(/٫/g,'.');
}
export function validateStocktakeQuantity(value,kind){
  const text=normalizeStocktakeQuantity(value);
  if(!text)return '';
  if(text.length>40||!/^\d+(?:\.\d{1,6})?$/.test(text)||Number(text)>1000000000)return 'أدخل كمية موجبة أو صفرًا، حتى 6 منازل عشرية.';
  if(kind==='product'&&text.includes('.')&&!/^\d+\.0+$/.test(text))return 'كمية المنتج يجب أن تكون عددًا صحيحًا.';
  return '';
}
function documentIsValid(value){
  return value?.schema_version===SCHEMA&&value?.id&&Number.isInteger(value.revision)&&value.revision>=1&&Array.isArray(value.lines)&&value.capabilities?.view===true;
}
function contextIsValid(value){
  return value?.schema_version===SCHEMA&&value.capabilities?.view===true&&Array.isArray(value.warehouses)&&Array.isArray(value.inventory_accounts)&&Array.isArray(value.variance_accounts);
}
function editorFor(doc){
  return {title:doc.title||'',lines:Object.fromEntries(doc.lines.map(row=>[row.id,{counted_quantity:row.counted_quantity??'',reason:row.reason||''}]))};
}
function changedLines(doc,editor){
  return doc.lines.filter(row=>{
    const input=editor.lines[row.id];
    return input&&(input.counted_quantity!==String(row.counted_quantity??'')||input.reason!==(row.reason||''));
  });
}
function dateLabel(value){
  if(!value)return 'غير مثبت';
  const date=new Date(value);
  return Number.isNaN(date.getTime())?String(value):date.toLocaleString('ar-SA',{dateStyle:'medium',timeStyle:'short'});
}
function PostingBlockers({items,enabled}){
  return <div className="mz-count-posting-note"><ShieldCheck size={20}/><div><strong>{enabled?'الترحيل متاح للمخوّل بعد الاعتماد والتأكيد':'الترحيل غير متاح حاليًا'}</strong><p>حفظ الجرد واعتماده لا يغيّران الكميات أو القيود. الترحيل خطوة مستقلة بصلاحية صريحة.</p>{items.length>0&&<ul>{items.map((item,index)=><li key={item.code||index}>{item.message||item.code}</li>)}</ul>}</div></div>;
}

const AUDIT_FIELD={title:'عنوان الجرد',status:'الحالة',reason:'سبب الفرق',review_reason:'سبب المراجعة',inventory_account_id:'حساب المخزون',variance_account_code:'حساب الفروقات',warehouse_ids:'المستودعات',submitted_by:'أرسله',submitted_at:'وقت الإرسال',approved_by:'اعتمده',approved_at:'وقت الاعتماد',approved_revision:'الإصدار المعتمد',reviewed_by:'راجعه',reviewed_at:'وقت المراجعة',posting_operation_id:'مرجع عملية الترحيل'};
function auditValue(value){
  if(value===null||value===undefined||value==='')return 'غير مسجل';
  if(Array.isArray(value))return value.join('، ');
  return STATUS[value]||String(value);
}
function AuditRow({row,label}){
  return <div className="mz-count-audit-row"><strong>{label}</strong>{row?<dl>
    <div><dt>المسجل</dt><dd>{display(row.recorded_quantity)} {unitName(row.unit)}</dd></div>
    <div><dt>الفعلي</dt><dd>{row.counted_quantity===null||row.counted_quantity===undefined?'لم يُجرد':`${row.counted_quantity} ${unitName(row.unit)}`}</dd></div>
    <div><dt>تكلفة الوحدة</dt><dd>{display(row.unit_cost)}</dd></div>
    {row.difference!==undefined&&<div><dt>الفرق / القيمة</dt><dd>{display(row.difference)} / {display(row.difference_value)}</dd></div>}
    <div><dt>السبب</dt><dd>{row.reason||'غير مسجل'}</dd></div>
  </dl>:<p>لم يكن البند ضمن هذا النطاق.</p>}</div>;
}
function AuditEvent({entry}){
  const [open,setOpen]=useState(false);
  return <li><strong>{({create:'إنشاء',save:'حفظ جزئي',posted:'ترحيل',...ACTION})[entry.action]||entry.action}</strong>
    <span>{dateLabel(entry.at)} · {entry.actor_name||entry.actor_id||'هوية غير مثبتة'} · إصدار {entry.revision}</span>
    {entry.from_status!==entry.to_status&&entry.to_status&&<p>{entry.from_status?`${STATUS[entry.from_status]||entry.from_status} ← `:''}{STATUS[entry.to_status]||entry.to_status}</p>}
    {entry.reason&&<p>{entry.reason}</p>}
    <details className="mz-count-audit-event" onToggle={event=>setOpen(event.currentTarget.open)}><summary>تفاصيل الحدث</summary>{open&&<>
      <p className="mz-count-reference">هوية الموظف: <bdi>{entry.actor_id||'غير مثبتة'}</bdi> · مرجع الطلب: <bdi>{entry.request_id||'غير مثبت'}</bdi></p>
      {Object.entries(entry.change_set?.fields||{}).map(([key,value])=><p className="mz-count-audit-field" key={key}><strong>{AUDIT_FIELD[key]||key}: </strong><span>{auditValue(value.before)} ← {auditValue(value.after)}</span></p>)}
      {entry.change_set?.lines?.map(change=><section className="mz-count-audit-change" key={change.line_id}><h4>{change.name||change.line_id} · <bdi>{change.after?.sku||change.before?.sku}</bdi></h4><div><AuditRow row={change.before} label="قبل التعديل"/><AuditRow row={change.after} label="بعد التعديل"/></div></section>)}
      {entry.snapshot?.length>0&&<details className="mz-count-audit-snapshot"><summary>{entry.action==='create'?'بنود المقارنة الأصلية':'البنود المثبتة لهذا الإجراء'} ({entry.snapshot.length})</summary>{entry.snapshot.map(row=><section className="mz-count-audit-change" key={row.id}><h4>{row.name||row.sku} · <bdi>{row.sku}</bdi></h4><p>{row.warehouse_name||row.warehouse_id} / <bdi>{row.location_code||row.location_id}</bdi></p><AuditRow row={row} label="بيانات البند"/></section>)}</details>}
      {entry.txn_group_id&&<p className="mz-count-reference">مرجع القيد: <bdi>{entry.txn_group_id}</bdi></p>}
      {entry.authorization_hash&&<p className="mz-count-reference">مرجع الاعتماد: <bdi>{entry.authorization_hash}</bdi></p>}
    </>}</details>
  </li>;
}

export default function StocktakeWorkbench({mode='stocktake',accessMode=null}){
  const [context,setContext]=useState(null),[items,setItems]=useState([]),[complete,setComplete]=useState(true);
  const [loading,setLoading]=useState(false),[reading,setReading]=useState(false),[busy,setBusy]=useState(false);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[search,setSearch]=useState(''),[statusFilter,setStatusFilter]=useState('all');
  const [createOpen,setCreateOpen]=useState(false),[createTitle,setCreateTitle]=useState(''),[warehouses,setWarehouses]=useState([]);
  const [locationIds,setLocationIds]=useState([]),[locationSearch,setLocationSearch]=useState('');
  const [document,setDocument]=useState(null),[editor,setEditor]=useState(null),[rowSearch,setRowSearch]=useState(''),[rowFilter,setRowFilter]=useState('all');
  const [action,setAction]=useState(null),[actionReason,setActionReason]=useState(''),[inventoryAccount,setInventoryAccount]=useState(''),[varianceAccount,setVarianceAccount]=useState(''),[confirmPost,setConfirmPost]=useState(false);
  const [pending,setPending]=useState(null),[conflict,setConflict]=useState(false);
  const lane=useRef(0),busyRef=useRef(false),pendingRef=useRef(null);
  const readOnly=accessMode==='read';
  const adjustments=mode==='adjustments';
  const locked=busy||Boolean(pending);
  const canCount=context?.capabilities?.count===true;
  const canEdit=Boolean(document&&canCount&&document.capabilities?.count===true&&editableStatus(document.status));
  const canReview=Boolean(document&&context?.capabilities?.review===true&&document.capabilities?.review===true&&['submitted','approved'].includes(document.status));
  const dirty=Boolean(document&&editor&&(editor.title!==(document.title||'')||changedLines(document,editor).length));
  const dirtyRowIds=useMemo(()=>new Set(document&&editor?changedLines(document,editor).map(row=>row.id):[]),[document,editor]);
  const blockers=useMemo(()=>{
    const entries=[...(context?.posting_blockers||[]),...(document?.posting_blockers||[])];
    return entries.filter((item,index)=>entries.findIndex(other=>(other.code||other.message)===(item.code||item.message))===index);
  },[context,document]);
  const postAllowed=Boolean(document?.status==='approved'&&context?.capabilities?.post===true&&document.capabilities?.post===true&&context.posting_enabled===true&&document.posting_enabled===true&&blockers.length===0&&document.posting_operation_id);
  const windowItems=useMemo(()=>items.filter(item=>!adjustments||item.status!=='draft').filter(item=>statusFilter==='all'||item.status===statusFilter).filter(item=>!search||[item.title,item.id,item.created_by].filter(Boolean).join(' ').toLowerCase().includes(search.toLowerCase())),[items,adjustments,statusFilter,search]);
  const visibleRows=useMemo(()=>!document||!editor?[]:document.lines.filter(row=>{
    const value=editor.lines[row.id]?.counted_quantity??'';
    if(rowFilter==='uncounted'&&value!=='')return false;
    if(rowFilter==='counted'&&value==='')return false;
    if(rowFilter==='variance'&&(row.difference==null||Number(row.difference)===0))return false;
    return !rowSearch||[row.name,row.sku,row.location_code,row.warehouse_name,row.preparation_state,typeof row.specifications==='object'?JSON.stringify(row.specifications):row.specifications].filter(Boolean).join(' ').toLowerCase().includes(rowSearch.toLowerCase());
  }),[document,editor,rowSearch,rowFilter]);

  function clearAuthority(){
    lane.current+=1;busyRef.current=false;pendingRef.current=null;
    setContext(null);setItems([]);setDocument(null);setEditor(null);setCreateOpen(false);setAction(null);setPending(null);setBusy(false);setLoading(false);setReading(false);
  }
  function deny(errorValue){
    clearAuthority();setNotice('');setError(inventoryStocktakeMessage(errorValue)||'لم تعد صلاحية الجرد متاحة.');
  }
  function install(doc){
    if(doc?.capabilities?.view===false){const revoked=new Error('stocktake_permission_revoked');revoked.response={status:403,data:{detail:'لم تعد صلاحية عرض هذا الجرد متاحة.'}};throw revoked;}
    if(!documentIsValid(doc))throw new Error('stocktake_invalid_document');
    setDocument(doc);setEditor(editorFor(doc));setItems(current=>[doc,...current.filter(item=>item.id!==doc.id)]);
    setInventoryAccount(doc.inventory_account_id||'');setVarianceAccount(doc.variance_account_code||'');
    setConflict(false);setAction(null);setActionReason('');setConfirmPost(false);setCreateOpen(false);
  }
  async function loadWorkspace(){
    const current=++lane.current;
    setContext(null);setItems([]);setDocument(null);setEditor(null);setCreateOpen(false);setAction(null);setError('');setNotice('');setLoading(true);
    try{
      const ctx=await loadInventoryStocktakeContext();if(current!==lane.current)return;
      if(!contextIsValid(ctx))throw new Error('stocktake_invalid_context');
      const saved=await listInventoryStocktakes();if(current!==lane.current)return;
      if(saved?.schema_version!==SCHEMA||saved.read_only!==true||!Array.isArray(saved.items))throw new Error('stocktake_invalid_list');
      setContext(ctx);setItems(saved.items);setComplete(saved.window_complete!==false);
    }catch(value){if(current===lane.current){setContext(null);setItems([]);setError(inventoryStocktakeMessage(value));}}
    finally{if(current===lane.current)setLoading(false);}
  }
  useEffect(()=>{
    clearAuthority();
    if(readOnly)return undefined;
    loadWorkspace();
    return ()=>{lane.current+=1;};
    // A new scope or page never inherits a pending document from the prior one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[mode,accessMode]);

  async function openDocument(id){
    if(locked||reading||!context)return;
    const current=lane.current;setReading(true);setError('');setNotice('');setRowSearch('');setRowFilter('all');
    try{const saved=await getInventoryStocktake(id);if(current===lane.current)install(saved);}
    catch(value){if(current===lane.current){if(isDenied(value))deny(value);else setError(inventoryStocktakeMessage(value));}}
    finally{if(current===lane.current)setReading(false);}
  }
  function closeDocument(){
    if(locked||reading)return;
    if(dirty){setAction('discard');return;}
    setDocument(null);setEditor(null);setAction(null);setError('');setConflict(false);
  }
  function beginAction(next){
    if(locked||reading)return;
    setAction(next);setActionReason('');setConfirmPost(false);setError('');
  }
  async function run(job){
    if(busyRef.current)return;
    const current=lane.current;busyRef.current=true;pendingRef.current=job;setPending(null);setBusy(true);setError('');setNotice('');
    try{
      let saved;
      if(job.kind==='create')saved=await createInventoryStocktake(job.body);
      else if(job.kind==='save')saved=await saveInventoryStocktake(job.id,job.body);
      else if(job.kind==='post')saved=await postInventoryStocktake(job.id,job.body);
      else saved=await actOnInventoryStocktake(job.id,job.body);
      if(current!==lane.current)return;
      // An invalid or lost response is uncertain too: retain the exact request for recovery.
      install(saved);pendingRef.current=null;setPending(null);setError('');
      setNotice(job.kind==='post'?'تم الترحيل وفق العملية المعتمدة.':job.kind==='save'?'حُفظ الجرد الجزئي. البنود الفارغة لم تُحتسب صفرًا.':job.kind==='create'?'أُنشئت مسودة من أرصدة المستودع الفعلية.':'حُفظت الخطوة في سجل الجرد.');
    }catch(value){
      if(current!==lane.current)return;
      if(isDenied(value)){deny(value);return;}
      setError(inventoryStocktakeMessage(value));
      if(uncertain(value))setPending(job);
      else{pendingRef.current=null;setPending(null);if(value?.response?.status===409)setConflict(true);}
    }finally{if(current===lane.current){busyRef.current=false;setBusy(false);}}
  }
  function create(event){
    event.preventDefault();if(locked||!canCount)return;
    if(!createTitle.trim()){setError('أدخل اسمًا للجرد.');return;}
    if(!warehouses.length){setError('حدد مستودعًا واحدًا على الأقل لنطاق الجرد.');return;}
    const available=(context.locations||[]).filter(location=>warehouses.includes(location.warehouse_id));
    if(locationIds.some(id=>!available.some(location=>location.id===id))){setError('راجع الخانات المحددة بعد تغيير المستودعات، أو ألغِ تحديدها لجرد المستودع.');return;}
    run({kind:'create',body:{request_id:newStocktakeRequestId(),title:createTitle.trim(),warehouse_ids:warehouses,location_ids:locationIds,row_ids:[]}});
  }
  function save(){
    if(locked||!canEdit||!editor)return;
    if(!editor.title.trim()){setError('أدخل اسمًا للجرد.');return;}
    const changes=changedLines(document,editor);
    for(const row of changes){const message=validateStocktakeQuantity(editor.lines[row.id].counted_quantity,row.kind);if(message){setError(`${row.name||row.sku}: ${message}`);return;}}
    run({kind:'save',id:document.id,body:{request_id:newStocktakeRequestId(),expected_revision:document.revision,title:editor.title.trim(),lines:changes.map(row=>({id:row.id,counted_quantity:normalizeStocktakeQuantity(editor.lines[row.id].counted_quantity)||null,unit:row.unit,reason:editor.lines[row.id].reason.trim()}))}});
  }
  async function confirmAction(){
    if(locked||!document||!action)return;
    if(action==='discard'){setDocument(null);setEditor(null);setAction(null);setError('');setConflict(false);return;}
    if(action==='reload'){
      const current=lane.current;setReading(true);setError('');
      try{const saved=await getInventoryStocktake(document.id);if(current===lane.current)install(saved);}
      catch(value){if(current===lane.current){if(isDenied(value))deny(value);else setError(inventoryStocktakeMessage(value));}}
      finally{if(current===lane.current)setReading(false);}
      return;
    }
    if(dirty){setError('احفظ مدخلات الجرد أولًا قبل متابعة هذه الخطوة.');return;}
    if(action==='post'){
      if(!postAllowed||!confirmPost)return;
      run({kind:'post',id:document.id,body:{operation_id:document.posting_operation_id,expected_revision:document.revision}});return;
    }
    if(['submit','refresh_snapshot'].includes(action)&&!canEdit)return;
    if(['approve','reject','return'].includes(action)&&!canReview)return;
    if(action==='approve'&&document.status!=='submitted')return;
    if(['reject','return'].includes(action)&&!actionReason.trim()){setError('اكتب سبب الرفض أو الإعادة للتعديل.');return;}
    if(action==='submit'){
      if(!document.lines.some(row=>row.counted_quantity!=null)){setError('أدخل كمية فعلية لبند واحد على الأقل ثم احفظ.');return;}
      const differences=document.lines.filter(row=>row.difference!=null&&Number(row.difference)!==0);
      if(differences.length&&(!inventoryAccount||!varianceAccount)){setError('اختر حساب المخزون وحساب فروقات الجرد من الحسابات المعتمدة.');return;}
      if(differences.some(row=>!row.reason?.trim())&&!actionReason.trim()){setError('اكتب سببًا للفروقات أو سببًا في كل بند مختلف.');return;}
    }
    const body={request_id:newStocktakeRequestId(),expected_revision:document.revision,action,reason:actionReason.trim()};
    if(action==='submit'){body.inventory_account_id=inventoryAccount||null;body.variance_account_code=varianceAccount||null;}
    run({kind:'action',id:document.id,body});
  }
  function updateLine(id,field,value){setEditor(current=>({...current,lines:{...current.lines,[id]:{...current.lines[id],[field]:value}}}));setError('');}
  const inlineError=error?<div role="alert" className="mz-count-error"><WarningCircle size={19}/><div>{error}{pending&&<p>لم تصل نتيجة مؤكدة. احتفظنا بنفس الطلب والمدخلات؛ أعد المحاولة لاسترجاع النتيجة قبل إغلاق النموذج.</p>}{conflict&&<p>مدخلاتك باقية. راجع النسخة المحفوظة أو احفظ نسخة من ملاحظاتك قبل تحديث المقارنة؛ لا يوجد تجاوز للإصدار.</p>}</div>{pending&&<button type="button" className="mz-inventory-button" disabled={busy} onClick={()=>run(pendingRef.current)}>إعادة الطلب نفسه</button>}</div>:null;

  if(readOnly)return <InventorySurface className="mz-count-empty"><ShieldCheck size={32}/><h2>الجرد يحتاج مساحة عمل مخوّلة</h2><p>هذه الصلاحية للعرض. راجع الأرصدة والتقارير من صفحات المخزون المتاحة لك.</p></InventorySurface>;
  return <div className="mz-count-workbench">
    <InventoryPageHeader title={adjustments?'تسويات المخزون':'الجرد'} description={adjustments?'راجع فروقات الجرد المعتمدة، واعتمد التسوية بصلاحيات مستقلة وسجل واضح.':'جرد المستودع في خطوات قصيرة: اختر النطاق، أدخل الكميات الفعلية، ثم أرسلها للمراجعة.'}>
      <button type="button" className="mz-inventory-button" disabled={loading||reading||locked} onClick={loadWorkspace}><ArrowClockwise size={18}/>تحديث</button>
      {!adjustments&&canCount&&<button type="button" className="mz-inventory-button is-primary" disabled={locked||reading} onClick={()=>{setCreateTitle('');setWarehouses([]);setLocationIds([]);setLocationSearch('');setCreateOpen(true);setError('');setNotice('');}}><Plus size={18}/>جرد جديد</button>}
    </InventoryPageHeader>
    {!document&&!createOpen&&inlineError}
    {notice&&<div role="status" className="mz-count-success"><CheckCircle size={20}/>{notice}</div>}
    {loading?<InventorySurface className="mz-count-empty"><p role="status">جارٍ قراءة صلاحيات الجرد والنطاق المتاح…</p></InventorySurface>:!context?<InventorySurface className="mz-count-empty"><ShieldCheck size={32}/><h2>تعذر فتح مساحة الجرد</h2><p>لا تُعرض مستندات أو إجراءات دون صلاحية مؤكدة من الخدمة.</p><button type="button" className="mz-inventory-button" onClick={loadWorkspace}>إعادة المحاولة</button></InventorySurface>:<>
      <PostingBlockers items={blockers} enabled={context.posting_enabled===true}/>
      <div className="mz-count-summary"><div><ClipboardText size={22}/><span>مستندات الجرد<strong>{items.length}</strong></span></div><div><ClockCounterClockwise size={22}/><span>بانتظار المراجعة<strong>{items.filter(item=>item.status==='submitted').length}</strong></span></div><div><ShieldCheck size={22}/><span>معتمد ولم يرحّل<strong>{items.filter(item=>item.status==='approved').length}</strong></span></div></div>
      <InventorySurface>
        <div className="mz-count-list-toolbar"><label className="mz-count-search"><MagnifyingGlass size={18}/><input aria-label="بحث مستندات الجرد" placeholder="ابحث بالاسم أو المرجع" value={search} onChange={event=>setSearch(event.target.value)}/></label><select aria-label="حالة مستند الجرد" value={statusFilter} onChange={event=>setStatusFilter(event.target.value)}><option value="all">جميع الحالات</option>{Object.entries(STATUS).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></div>
        {!complete&&<p className="mz-count-window-note">تعرض الخدمة أحدث 200 مستند؛ هذه القائمة ليست كامل التاريخ.</p>}
        {windowItems.length===0?<div className="mz-count-empty"><ClipboardText size={38}/><h2>{items.length?'لا توجد نتائج مطابقة':adjustments?'لا توجد فروقات محفوظة للمراجعة':'ابدأ جردًا من مستودعك'}</h2><p>{items.length?'غيّر البحث أو الحالة لعرض المستندات.':'تظهر هنا مستندات الجرد المحفوظة من الخدمة. البنود غير المعدودة تبقى دون كمية فعلية.'}</p></div>:<div className="mz-count-table-scroll"><table className="mz-count-documents"><thead><tr><th>مستند الجرد</th><th>الحالة</th><th>بنود معدودة / فروقات</th><th>قيمة زيادة / نقص</th><th>آخر تحديث</th><th><span className="sr-only">فتح</span></th></tr></thead><tbody>{windowItems.map(item=><tr key={item.id}><td><strong>{item.title}</strong><small dir="ltr">{item.id}</small></td><td><span className={`mz-count-status is-${item.status}`}>{STATUS[item.status]||item.status}</span></td><td>{display(item.counted_lines)} / {display(item.variance_lines)}</td><td><span dir="ltr">{display(item.increase_value)} / {display(item.decrease_value)}</span><small>ر.س — من الخدمة</small></td><td>{dateLabel(item.updated_at)}</td><td><button type="button" className="mz-inventory-button" disabled={reading||locked} onClick={()=>openDocument(item.id)}>فتح الجرد</button></td></tr>)}</tbody></table></div>}
      </InventorySurface>
    </>}

    <InventorySheet open={createOpen} onClose={()=>{if(!locked){setCreateOpen(false);setError('');}}} busy={locked} title="جرد جديد" description="اختر المستودعات لقراءة لقطة فعلية من أرصدتها. لا تتغير الكميات عند إنشاء المسودة." footer={<><small>حتى 500 بند في المسودة</small><button type="submit" form="mz-count-create" className="mz-inventory-button is-primary" disabled={locked}>{busy?'جارٍ قراءة الأرصدة…':'قراءة البنود وبدء الجرد'}</button></>}>
      <form id="mz-count-create" className="mz-count-form" onSubmit={create}>
        {inlineError}<label><span>اسم الجرد <b>*</b></span><input autoFocus maxLength={160} required value={createTitle} disabled={locked} onChange={event=>setCreateTitle(event.target.value)} placeholder="مثال: جرد مستودع الرياض"/></label>
        <fieldset disabled={locked}><legend>نطاق الجرد <b>*</b></legend><p>اختر من المستودعات المسموحة لك. ستظهر الأصناف والمكونات والوحدات كما هي مسجلة.</p><div className="mz-count-warehouse-options">{context?.warehouses.map(warehouse=><label key={warehouse.id}><input type="checkbox" checked={warehouses.includes(warehouse.id)} onChange={event=>setWarehouses(current=>event.target.checked?[...current,warehouse.id]:current.filter(id=>id!==warehouse.id))}/><span>{warehouse.name}</span></label>)}</div>{!context?.warehouses.length&&<p>لا توجد مستودعات مخوّلة ضمن النطاق الحالي.</p>}</fieldset>
        <div className="mz-count-help">بعد قراءة النطاق، ابحث بالاسم أو الرقم المخزني أو الخانة وأدخل فقط البنود التي جردتها. ترك الكمية فارغة يعني «لم يُجرد».</div>
        {!!context?.locations?.length&&<details className="mz-count-location-scope"><summary>جرد خانات محددة فقط (اختياري)</summary><p>للمستودعات الكبيرة، ابدأ بجزء صغير ثم أكمل بقية الخانات في جرد آخر. دون تحديد خانات يُقرأ نطاق المستودعات المختارة كاملًا، حتى 500 بند.</p><input aria-label="بحث خانات نطاق الجرد" value={locationSearch} onChange={event=>setLocationSearch(event.target.value)} placeholder="ابحث برمز الخانة…"/><div className="mz-count-location-options">{context.locations.filter(location=>warehouses.includes(location.warehouse_id)&&String(location.label||location.id).includes(locationSearch.trim())).slice(0,100).map(location=><label key={location.id}><input type="checkbox" aria-label={'جرد الخانة '+location.label} checked={locationIds.includes(location.id)} onChange={event=>setLocationIds(current=>event.target.checked?[...current,location.id]:current.filter(id=>id!==location.id))}/><span>{location.label} · {location.stock_lines} بنود</span></label>)}</div><small>الخانات المحددة: {locationIds.length}. يعرض البحث حتى 100 خانة مطابقة.</small>{locationIds.length>0&&<button type="button" onClick={()=>setLocationIds([])}>إلغاء تحديد الخانات</button>}</details>}
      </form>
    </InventorySheet>

    <InventorySheet open={Boolean(document)} onClose={closeDocument} busy={locked||reading} wide title={document?.title||'مستند الجرد'} description={document?`الإصدار ${document.revision} • ${STATUS[document.status]||document.status} • البنود الفارغة لا تُحتسب صفرًا.`:''} footer={document&&<>
      <div className="mz-count-footer-state">{busy||reading?'جارٍ حفظ الخطوة…':pending?'بانتظار استرجاع نتيجة الطلب':dirty?'توجد مدخلات غير محفوظة':`الإصدار المحفوظ ${document.revision}`}</div>
      <div className="mz-count-footer-actions">{action?<><button type="button" className="mz-inventory-button" disabled={locked||reading} onClick={()=>{setAction(null);setError('');}}>رجوع</button><button type="button" className={`mz-inventory-button ${action==='reject'?'is-danger':'is-primary'}`} disabled={locked||reading||(action==='post'&&(!postAllowed||!confirmPost))} onClick={confirmAction}>{ACTION[action]}</button></>:<>
        {canEdit&&<><button type="button" className="mz-inventory-button" disabled={locked||reading||!dirty} onClick={save}>حفظ الجرد الجزئي</button><button type="button" className="mz-inventory-button is-primary" disabled={locked||reading||dirty||conflict} onClick={()=>beginAction('submit')}>إرسال للمراجعة</button></>}
        {canReview&&<><button type="button" className="mz-inventory-button" disabled={locked} onClick={()=>beginAction('return')}>إعادة للتعديل</button><button type="button" className="mz-inventory-button" disabled={locked} onClick={()=>beginAction('reject')}>رفض</button>{document.status==='submitted'&&<button type="button" className="mz-inventory-button is-primary" disabled={locked||conflict} onClick={()=>beginAction('approve')}>اعتماد</button>}</>}
        {document.status==='approved'&&<button type="button" className="mz-inventory-button is-primary" disabled={locked||!postAllowed||conflict} onClick={()=>beginAction('post')}>مراجعة ترحيل الفروق</button>}
        {!canEdit&&!canReview&&document.status!=='approved'&&<button type="button" className="mz-inventory-button" disabled={locked} onClick={closeDocument}>إغلاق الجرد</button>}
      </>}</div>
    </>}>
      {document&&editor&&<div className="mz-count-editor">
        {inlineError}{notice&&<div role="status" className="mz-count-success"><CheckCircle size={18}/>{notice}</div>}
        <div className="mz-count-document-meta"><span className={`mz-count-status is-${document.status}`}>{STATUS[document.status]||document.status}</span><span>{display(document.counted_lines)} بند معدود</span><span>{display(document.variance_lines)} فرق</span><span>{display(document.total_lines??document.lines.length)} بند في النطاق</span></div>
        {(document.inventory_account_id||document.variance_account_code||document.reason||document.review_reason)&&<div className="mz-count-help"><div>حساب المخزون: {context.inventory_accounts.find(account=>account.entity_id===document.inventory_account_id)?.label||display(document.inventory_account_id)}</div><div>حساب الفروقات: {context.variance_accounts.find(account=>account.code===document.variance_account_code)?.name||display(document.variance_account_code)}</div>{document.reason&&<p>سبب الفروقات: {document.reason}</p>}{document.review_reason&&<p>ملاحظة المراجع: {document.review_reason}</p>}</div>}
        <label className="mz-count-title-field"><span>اسم الجرد</span><input aria-label="اسم مستند الجرد" maxLength={160} disabled={!canEdit||locked||reading||Boolean(action)} value={editor.title} onChange={event=>setEditor(current=>({...current,title:event.target.value}))}/></label>
        {action&&<section className="mz-count-confirm" aria-label={ACTION[action]}>
          <h3>{ACTION[action]}</h3>
          {action==='submit'?<><p>يُرسل ما حفظته فقط. البنود غير المعدودة لا تُسوّى، والاعتماد والترحيل خطوتان مستقلتان.</p><div className="mz-count-account-fields"><label><span>حساب المخزون</span><select aria-label="حساب المخزون" value={inventoryAccount} disabled={locked} onChange={event=>setInventoryAccount(event.target.value)}><option value="">اختر الحساب المعتمد</option>{context.inventory_accounts.map(account=><option key={account.entity_id} value={account.entity_id}>{account.label||account.entity_id}</option>)}</select></label><label><span>حساب فروقات الجرد</span><select aria-label="حساب فروقات الجرد" value={varianceAccount} disabled={locked} onChange={event=>setVarianceAccount(event.target.value)}><option value="">اختر من الحسابات القائمة</option>{context.variance_accounts.map(account=><option key={account.code} value={account.code}>{account.code} — {account.name}</option>)}</select></label></div>{(!context.inventory_accounts.length||!context.variance_accounts.length)&&<p className="mz-count-help">تحتاج الفروقات إلى حسابات معتمدة موجودة. لا ينشئ هذا النموذج حسابات تلقائيًا.</p>}</>:action==='post'?<><p>سيُعدّل الترحيل الكميات الفعلية وينشئ القيود وفق الجرد المعتمد. تعيد الخدمة التحقق من الصلاحيات والأرصدة والتكاليف والحجوزات والفترة المحاسبية قبل التنفيذ.</p><dl className="mz-count-post-proof"><div><dt>مستند الجرد</dt><dd>{document.title}</dd></div><div><dt>عملية الترحيل المعتمدة</dt><dd dir="ltr">{document.posting_operation_id}</dd></div><div><dt>الإصدار</dt><dd>{document.revision}</dd></div></dl><label className="mz-count-post-consent"><input type="checkbox" checked={confirmPost} disabled={locked} onChange={event=>setConfirmPost(event.target.checked)}/><span>راجعت الفروقات وأؤكد ترحيل العملية المعتمدة.</span></label></>:action==='refresh_snapshot'?<p>تُقرأ الأرصدة والتكاليف الحالية من جديد. تبقى الكميات المعدودة للبنود المتطابقة، وتُلغى المقارنة السابقة لتُراجع الفروقات مجددًا.</p>:action==='reload'?<p>ستستبدل هذه الخطوة المدخلات المعروضة بالنسخة المحفوظة لدى الخدمة. انسخ ملاحظاتك غير المحفوظة قبل المتابعة.</p>:action==='discard'?<p>ستُغلق المدخلات غير المحفوظة. يبقى المستند المحفوظ كما هو لدى الخدمة.</p>:action==='approve'?<p>يعتمد هذا الإجراء الجرد للمراجعة فقط؛ لا يرحّل الفروقات ولا يغيّر الرصيد. تحتاج خطوة الترحيل إلى تأكيد مستقل.</p>:<p>سيُسجل السبب مع هوية المراجع، {action==='return'?'ويُعاد المستند لإكمال الجرد.':'ويُغلق المستند بحالة مرفوض.'}</p>}
          {!['post','reload','discard'].includes(action)&&<label><span>{['reject','return'].includes(action)?'سبب المراجعة *':action==='submit'?'سبب عام للفروقات (أو سبب لكل بند)':'ملاحظة المراجعة'}</span><textarea aria-label="سبب إجراء الجرد" maxLength={1000} value={actionReason} disabled={locked} onChange={event=>setActionReason(event.target.value)} rows={2}/></label>}
        </section>}
        {conflict&&!action&&<div className="mz-count-conflict-actions"><button type="button" className="mz-inventory-button" disabled={locked} onClick={()=>beginAction('reload')}>قراءة النسخة المحفوظة</button>{canEdit&&<button type="button" className="mz-inventory-button" disabled={locked||dirty} onClick={()=>beginAction('refresh_snapshot')}>تحديث المقارنة</button>}</div>}
        <div className="mz-count-editor-toolbar"><label className="mz-count-search"><MagnifyingGlass size={18}/><input aria-label="بحث بنود الجرد" placeholder="الصنف، الرقم المخزني، أو الخانة" value={rowSearch} onChange={event=>setRowSearch(event.target.value)}/></label><select aria-label="عرض بنود الجرد" value={rowFilter} onChange={event=>setRowFilter(event.target.value)}><option value="all">كل البنود</option><option value="uncounted">لم يُجرد</option><option value="counted">تم إدخال كمية</option><option value="variance">فروقات محفوظة</option></select></div>
        <p className="mz-count-help">الوحدة والتكلفة من السجل الأصلي. أدخل 0 لإثبات عدم وجود كمية، واترك الحقل فارغًا إذا لم تجرد البند. لا تُجمع كميات بوحدات مختلفة.</p>
        <div className="mz-count-lines" aria-label="بنود الجرد">{visibleRows.length?visibleRows.map(row=>{
          const input=editor.lines[row.id],changed=dirtyRowIds.has(row.id);
          const quantityError=validateStocktakeQuantity(input.counted_quantity,row.kind);
          return <article className="mz-count-line" key={row.id} data-row-id={row.id}>
            <header><div><strong>{row.name||'اسم غير مثبت'}</strong><small><bdi>{row.sku||'دون رقم مخزني'}</bdi> · {row.kind==='component'?'مكوّن':'منتج'}</small></div><span>{row.warehouse_name||'مستودع غير مثبت'}<small dir="ltr">{row.location_code||'خانة غير مثبتة'}</small></span></header>
            {row.specifications&&<div className="mz-count-specifications">{typeof row.specifications==='object'?Object.entries(row.specifications).map(([key,value])=><span key={key}>{key}: {typeof value==='object'?JSON.stringify(value):String(value)}</span>):String(row.specifications)}</div>}
            <div className="mz-count-quantity-grid"><div><span>الكمية المسجلة</span><strong><bdi>{display(row.recorded_quantity)}</bdi> <small>{unitName(row.unit)}</small></strong></div><label><span>الكمية الفعلية</span><div className="mz-count-quantity-input"><input aria-label={`الكمية الفعلية ${row.sku||row.id}`} aria-invalid={Boolean(quantityError)} type="text" inputMode={row.kind==='product'?'numeric':'decimal'} dir="ltr" value={input.counted_quantity} maxLength={40} placeholder="لم يُجرد" disabled={!canEdit||locked||reading||Boolean(action)} onChange={event=>updateLine(row.id,'counted_quantity',event.target.value)}/><span>{unitName(row.unit)}</span></div>{quantityError&&<small className="mz-count-field-error">{quantityError}</small>}</label><div><span>الفرق المحفوظ</span><strong className={row.difference!=null&&Number(row.difference)!==0?'has-variance':''}>{changed?'يُحسب بعد الحفظ':row.difference==null?'لم يُجرد':<><bdi>{row.difference}</bdi> <small>{unitName(row.unit)}</small></>}</strong></div></div>
            <label className="mz-count-row-reason"><span>سبب الفرق / ملاحظة</span><input aria-label={`سبب الفرق ${row.sku||row.id}`} maxLength={500} placeholder="مثال: تلف عند الفحص" value={input.reason} disabled={!canEdit||locked||reading||Boolean(action)} onChange={event=>updateLine(row.id,'reason',event.target.value)}/></label>
            <footer><span>تكلفة الوحدة: <bdi>{display(row.unit_cost)}</bdi> ر.س</span><span>قيمة الفرق المحفوظ: <bdi>{changed?'تُحسب بعد الحفظ':display(row.difference_value)}</bdi> ر.س</span></footer>
          </article>;
        }):<div className="mz-count-empty"><p>لا توجد بنود تطابق البحث أو التصفية.</p></div>}</div>
        {document.status==='approved'&&<><PostingBlockers items={blockers} enabled={postAllowed}/>{context?.capabilities?.post!==true&&<p className="mz-count-help">ترحيل الفروقات يحتاج صلاحية صريحة. الاعتماد وحده لا يمنحها.</p>}{!document.posting_operation_id&&<p className="mz-count-help">لا يوجد معرّف ترحيل معتمد من الخدمة لهذا الإصدار.</p>}</>}
        {canEdit&&!action&&<div className="mz-count-secondary-actions"><button type="button" className="mz-inventory-button" disabled={locked||dirty} onClick={()=>beginAction('refresh_snapshot')}><ArrowClockwise size={17}/>تحديث المقارنة من المستودع</button><span>احفظ المدخلات قبل تحديث المقارنة.</span></div>}
        <details className="mz-count-audit"><summary><ClockCounterClockwise size={18}/>سجل الجرد والمراجعة</summary><p className="mz-count-reference">المرجع: <bdi>{document.id}</bdi></p>{document.audit?.length?<ol>{document.audit.map((entry,index)=><AuditEvent key={entry.request_id||index} entry={entry}/>)}</ol>:<p>لا توجد أحداث معروضة من الخدمة.</p>}</details>
      </div>}
    </InventorySheet>
  </div>;
}
