export function createBranchPicker({id, label, load, onSelect}) {
 const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
 const root=el('div','branch-picker');
 const trigger=el('button','branch-trigger');trigger.type='button';
 trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-expanded','false');
 const caption=el('span','','选择分支');const arrow=el('span','branch-chevron','⌄');arrow.setAttribute('aria-hidden','true');trigger.append(caption,arrow);
 const panel=el('div','branch-panel');panel.id=`${id}-branch-panel`;panel.hidden=true;panel.setAttribute('role','dialog');panel.setAttribute('aria-label',`${label} 分支选择`);trigger.setAttribute('aria-controls',panel.id);
 const searchRow=el('div','branch-search-row');
 const search=el('input');search.type='search';search.placeholder='搜索分支';search.autocomplete='off';search.setAttribute('role','combobox');search.setAttribute('aria-label',`${label} 搜索分支`);search.setAttribute('aria-autocomplete','list');search.setAttribute('aria-expanded','false');
 const refresh=el('button','branch-refresh','↻');refresh.type='button';refresh.title='刷新分支';refresh.setAttribute('aria-label',`刷新 ${label} 分支`);
 searchRow.append(search,refresh);
 const status=el('div','branch-picker-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
 const list=el('div','branch-list');list.id=`${id}-branch-list`;list.setAttribute('role','listbox');list.setAttribute('aria-label',`${label} 分支`);search.setAttribute('aria-controls',list.id);
 panel.append(searchRow,status,list);root.append(trigger,panel);
 let branches=[],filtered=[],selected=null,active=-1,loaded=false,loading=false,generation=0,error='';
 function syncTrigger(){caption.textContent=selected?.name||'选择分支';trigger.setAttribute('aria-label',`${label} 分支：${selected?.name||'未选择'}`);}
 function close(restoreFocus=false){panel.hidden=true;trigger.setAttribute('aria-expanded','false');search.setAttribute('aria-expanded','false');search.removeAttribute('aria-activedescendant');if(restoreFocus)trigger.focus();}
 function activate(index,scroll=false){
  active=index;const options=[...list.children];
  options.forEach((option,i)=>option.classList.toggle('is-active',i===active));
  if(options[active]){search.setAttribute('aria-activedescendant',options[active].id);if(scroll)options[active].scrollIntoView({block:'nearest'});}else search.removeAttribute('aria-activedescendant');
 }
 function choose(branch){selected=branch;syncTrigger();onSelect(branch);close(true);}
 function render(){
  const query=search.value.trim().toLowerCase();filtered=branches.filter(b=>b.name.toLowerCase().includes(query));
  list.replaceChildren();
  filtered.forEach((branch,i)=>{
   const option=el('div','branch-option');option.id=`${id}-branch-option-${i}`;option.setAttribute('role','option');option.setAttribute('aria-selected',String(branch.name===selected?.name));
   const name=el('span','branch-option-name',branch.name);const mark=el('span','branch-option-mark',branch.name===selected?.name?'✓':'');mark.setAttribute('aria-hidden','true');option.append(name,mark);
   option.addEventListener('mousedown',e=>e.preventDefault());option.addEventListener('click',()=>choose(branch));list.append(option);
  });
  activate(filtered.findIndex(b=>b.name===selected?.name));
  status.textContent=loading?'正在加载分支…':error||(filtered.length?'':loaded?(query?'没有匹配的分支':'仓库没有可用分支'):'');
  status.hidden=!status.textContent;status.classList.toggle('error',Boolean(error));list.setAttribute('aria-busy',String(loading));refresh.disabled=loading;
 }
 async function reload(){
  const ticket=++generation;loading=true;error='';render();
  try{
   const result=await load();if(ticket!==generation)return;
   if(!Array.isArray(result)||result.some(b=>!b||typeof b.name!=='string'))throw new Error('分支列表格式无效');
   branches=result;loaded=true;
   if(selected){selected=branches.find(b=>b.name===selected.name)||null;syncTrigger();onSelect(selected);}
  }catch(e){if(ticket!==generation)return;error=`${e.message}，点击刷新重试`;}
  finally{if(ticket===generation){loading=false;render();}}
 }
 function open(){panel.hidden=false;trigger.setAttribute('aria-expanded','true');search.setAttribute('aria-expanded','true');search.value='';render();search.focus();if(!loaded&&!loading)void reload();}
 trigger.addEventListener('click',()=>panel.hidden?open():close());
 trigger.addEventListener('keydown',e=>{if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();open();}});
 refresh.addEventListener('click',()=>{search.focus();void reload();});
 search.addEventListener('input',render);
 search.addEventListener('keydown',e=>{
  if(e.key==='ArrowDown'||e.key==='ArrowUp'){
   e.preventDefault();if(!filtered.length)return;
   const next=active<0?(e.key==='ArrowDown'?0:filtered.length-1):(active+(e.key==='ArrowDown'?1:-1)+filtered.length)%filtered.length;activate(next,true);
  }else if(e.key==='Enter'){e.preventDefault();if(filtered[active])choose(filtered[active]);}
 });
 panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);}});
 document.addEventListener('pointerdown',e=>{if(!root.contains(e.target))close();});
 root.addEventListener('focusout',e=>{if(!root.contains(e.relatedTarget))close();});
 syncTrigger();
 return {element:root,reset(){generation++;branches=[];selected=null;loaded=false;loading=false;error='';search.value='';syncTrigger();render();close();onSelect(null);}};
}
