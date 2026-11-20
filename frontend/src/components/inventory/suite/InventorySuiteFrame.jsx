import {useState} from 'react';
import {Link} from 'react-router-dom';
import {SquaresFour,Cube,Stack,Package,Plus,Receipt,ClipboardText,Warehouse,Notebook,SlidersHorizontal,ArrowsLeftRight,ChartBar,List,DotsThree,CaretLeft,X} from '@phosphor-icons/react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import {useOptionalAuth} from '../../../context/AuthContext';
import {grantedWebLink,managedWebPages} from '../../../lib/webPageAccess';
import {LogoIcon} from '../../MezanLogo';
import './inventorySuite.css';

export const INVENTORY_SUITE_NAV=[
  {id:'home',label:'الرئيسية',to:'/inventory-v2',Icon:SquaresFour},
  {id:'catalog',label:'الأصناف',to:'/inventory-v2?tab=stock',Icon:Cube},
  {id:'components',label:'المكونات',to:'/components-v2',Icon:Stack},
  {id:'bundles',label:'البكجات',to:'/products-v2?inventory_view=bundles',Icon:Package},
  {id:'entry',label:'إضافة المخزون',to:'/inventory-v2?tab=opening',Icon:Plus},
  {id:'purchases',label:'المشتريات',to:'/purchase-invoices',Icon:Receipt},
  {id:'receiving',label:'الاستلام',to:'/inventory-receiving-v2',Icon:ClipboardText},
  {id:'warehouses',label:'المستودعات',to:'/components-v2?workspace=warehouse',Icon:Warehouse},
  {id:'stocktake',label:'الجرد',to:'/inventory-v2?tab=stocktake',Icon:Notebook},
  {id:'adjustments',label:'التسويات',to:'/inventory-v2?tab=adjustments',Icon:SlidersHorizontal},
  {id:'movements',label:'حركة المخزون',to:'/inventory-v2?tab=movements',Icon:ArrowsLeftRight},
  {id:'reports',label:'التقارير',to:'/inventory-v2?tab=reports',Icon:ChartBar},
];

export function inventoryNavigationForUser(user){
  const pages=managedWebPages(user);
  if(pages)return INVENTORY_SUITE_NAV.filter(item=>grantedWebLink(pages,item.to));
  if(user?.is_owner===true)return INVENTORY_SUITE_NAV;
  return INVENTORY_SUITE_NAV.filter(item=>['home','receiving'].includes(item.id));
}

export default function InventorySuiteFrame({active='home',children}){
  const {user}=useOptionalAuth()||{};
  const items=inventoryNavigationForUser(user),[more,setMore]=useState(false),[mobile,setMobile]=useState(false);
  const primary=items.filter(item=>['home','catalog','components','purchases','receiving','warehouses'].includes(item.id));
  const secondary=items.filter(item=>!primary.some(p=>p.id===item.id));
  const link=item=><Link key={item.id} to={item.to} onClick={()=>{setMore(false);setMobile(false);}} className={item.id===active?'is-active':''} aria-current={item.id===active?'page':undefined}><item.Icon size={18}/>{item.label}</Link>;
  return <div className="mz-inventory-ui" dir="rtl" data-testid="inventory-suite" data-inventory-page={active}>
    <nav className="mz-inventory-nav" aria-label="نظام المخزون">
      <Link to="/inventory-v2" className="mz-inventory-brand"><LogoIcon size={34}/><span><strong>مخزون ميزان</strong><small>ميزان 2</small></span></Link>
      <div className="mz-inventory-nav-pages">{primary.map(link)}{secondary.length>0&&<div className="mz-inventory-more"><button type="button" className={secondary.some(i=>i.id===active)?'is-active':''} aria-expanded={more} onClick={()=>setMore(value=>!value)}><DotsThree size={20}/>المزيد</button>{more&&<><button type="button" className="mz-menu-dismiss" aria-label="إغلاق قائمة المخزون" onClick={()=>setMore(false)}/><div className="mz-inventory-menu">{secondary.map(link)}</div></>}</div>}</div>
      <button type="button" className="mz-inventory-mobile-menu" aria-label="صفحات المخزون" onClick={()=>setMobile(true)}><List size={24}/></button>
    </nav>
    <div className="mz-inventory-content">{children}</div>
    <InventorySheet open={mobile} onClose={()=>setMobile(false)} title="صفحات المخزون" description="اختر مساحة العمل المطلوبة."><nav className="mz-inventory-route-list" aria-label="صفحات نظام المخزون">{items.map(link)}</nav></InventorySheet>
  </div>;
}

export function InventoryPageHeader({title,description,children}){
  return <header className="mz-inventory-page-header"><div><div className="mz-inventory-eyebrow">ميزان 2 <CaretLeft size={12}/> المخزون</div><h1>{title}</h1>{description&&<p>{description}</p>}</div>{children&&<div className="mz-inventory-page-actions">{children}</div>}</header>;
}
export function InventorySurface({children,className='',...props}){return <section {...props} className={`mz-inventory-surface ${className}`}>{children}</section>;}
export function InventorySheet({open,onClose,title,description,children,footer,wide=false,busy=false}){
  return <DialogPrimitive.Root open={open} onOpenChange={value=>{if(!value&&!busy)onClose();}}><DialogPrimitive.Portal><DialogPrimitive.Overlay className="mz-inventory-overlay"/><DialogPrimitive.Content dir="rtl" className={`mz-inventory-sheet ${wide?'is-wide':''}`} onEscapeKeyDown={event=>{if(busy)event.preventDefault();}} onInteractOutside={event=>{if(busy)event.preventDefault();}}>
    <header><DialogPrimitive.Title>{title}</DialogPrimitive.Title><DialogPrimitive.Description>{description||'راجع البيانات ثم احفظ التغييرات.'}</DialogPrimitive.Description><DialogPrimitive.Close className="mz-inventory-sheet-close" aria-label="إغلاق" disabled={busy}><X size={21}/></DialogPrimitive.Close></header>
    <div className="mz-inventory-sheet-body mz-inventory-ui" dir="rtl">{children}</div>{footer&&<footer>{footer}</footer>}
  </DialogPrimitive.Content></DialogPrimitive.Portal></DialogPrimitive.Root>;
}
