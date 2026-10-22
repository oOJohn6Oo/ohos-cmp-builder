import {publicationURL,validateCredentials,validateUsername,validatePassword} from './credential-crypto.js';
import {createBranchPicker} from './branch-picker.js';
import {buildRequest,submitBuild,getBuildStatus,forgetFailedBuild,workflowURL,normalizeSuffix,validSuffix} from './build-client.js';
const $ = (id) => document.getElementById(id);
const repositories = {public:'https://maven.eazytec-cloud.com/nexus/repository/maven-public',inner:'https://maven.eazytec-cloud.com/nexus/repository/maven-inner'};
const deps = [
 ['kotlin','Kotlin','org.jetbrains.kotlin','kotlin-gradle-plugin'],
 ['skiko','Skiko OHOS','org.jetbrains.skiko','skiko-ohosarm64-api'],
 ['serialization','Serialization','org.jetbrains.kotlinx','kotlinx-serialization-core'],
 ['coroutines_core','Coroutines · Core','org.jetbrains.kotlinx','kotlinx-coroutines-core'],
 ['coroutines_cmp','Coroutines · CMP','org.jetbrains.kotlinx','kotlinx-coroutines-core'],
 ['okio','Okio','com.squareup.okio','okio'],
 ['atomicfu','AtomicFU','org.jetbrains.kotlinx','atomicfu'],
 ['datetime','DateTime','org.jetbrains.kotlinx','kotlinx-datetime'],
];
const state = {branches:{}, dependencies:{}};
function element(tag, text, cls) {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
const dependencyRepositories=['inner','public'];
const platformLabels={android:'Android',ohos:'OHOS',uikit:'UIKit',macos:'macOS'};
function selectedPlatforms(){return [...document.querySelectorAll('input[name="platform"]:checked')].map(input=>input.value);}
function suffix(){return normalizeSuffix($('suffix').value);}
function updateSummary(){
 const platforms=selectedPlatforms();
 for(const id of ['build-debug','build-trace','build-ltpo'])$(id).disabled=!platforms.includes('ohos');
 const items=[['CMP Core',state.branches.core?.name||'未选择'],['CMP',state.branches.cmp?.name||'未选择'],['构建平台',platforms.map(value=>platformLabels[value]).join(' / ')||'未选择'],['依赖覆盖',`${Object.values(state.dependencies).filter(x=>x.mode!=='default').length} 项`],['发布版本',suffix()?`1.12.0-${suffix()}`:'—'],['发布目标',$('publish-url').value.trim()||'未填写']];
 $('summary').replaceChildren(...items.flatMap(([a,b])=>[element('dt',a),element('dd',b)]));
 $('full-version').textContent=suffix()?`1.12.0-${suffix()}`:'1.12.0-…';
 const suffixInvalid=Boolean($('suffix').value)&&!validSuffix(suffix());
 $('suffix-message').hidden=!suffixInvalid;
 $('suffix-message').textContent=suffixInvalid?'后缀须以数字结尾，可用字母、数字、点、横线和下划线（最多 64 位），勿填写完整版本号':'';
 $('suffix').setAttribute('aria-invalid',String(suffixInvalid));
 const dependencies=Object.values(state.dependencies);
 const failed=dependencies.filter(d=>d.error).length;
 const loading=dependencies.some(d=>d.loading);
 $('dependency-state').textContent=failed?`${failed} 项待检查`:loading?'查询中…':`${dependencies.length} 项`;
 $('dependency-state').classList.toggle('error',failed>0);
 $('reset-modes').setAttribute('aria-pressed',String(dependencies.length>0&&dependencies.every(d=>d.mode==='default')));
 $('latest-modes').setAttribute('aria-pressed',String(dependencies.length>0&&dependencies.every(d=>d.mode==='latest')));
}
async function request(url) {
 const response=await fetch(url,{signal:AbortSignal.timeout(20000),credentials:'omit',cache:'no-store'});
 if(!response.ok){const error=new Error(response.status===404?'资源不存在或没有访问权限':`请求失败（HTTP ${response.status}）`);error.status=response.status;throw error;}
 return response;
}
for(const [id,label,initial] of [['core','CMP Core','CPF-KMP-CMP/compose-multiplatform-core'],['cmp','CMP','CPF-KMP-CMP/compose-multiplatform']]) {
 const box=element('div',undefined,'branch-block');
 const title=element('h3',label);const field=element('label',undefined,'field');
 const repo=element('input');repo.value=initial;repo.placeholder='GitCode 仓库 · owner/repository';repo.spellcheck=false;repo.setAttribute('aria-label',`${label} 仓库路径`);field.append(repo);
 const picker=createBranchPicker({id,label,
  load:async()=>{
   const path=repo.value.trim();
   if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(path))throw new Error('仓库路径需为 owner/repository');
   const all=[];
   for(let page=1;page<=100;page++){
    const response=await request(`https://api.gitcode.com/api/v5/repos/${path}/branches?per_page=100&page=${page}`);
    const list=await response.json();if(!Array.isArray(list))throw new Error('接口返回了无法识别的数据');
    all.push(...list);if(list.length<100)return all;
   }
   throw new Error('分支数量超出当前加载上限');
  },
  onSelect:branch=>{
   if(branch)state.branches[id]={repository:repo.value.trim(),name:branch.name,sha:branch.commit?.sha||branch.commit?.id};
   else delete state.branches[id];
   updateSummary();
  }
 });
 repo.addEventListener('input',()=>picker.reset());
 box.append(title,field,picker.element);$('branches').append(box);
}
for(const [id,label,group,artifact] of deps){
 const row=element('div',undefined,'dependency');
 const info=element('div');info.append(element('strong',label));
 const mode=element('select');mode.setAttribute('aria-label',`${label} 版本模式`);for(const [v,t] of [['default','分支默认'],['latest','最新 release'],['custom','自定义']])mode.append(new Option(t,v));
 const valueBox=element('div');const version=element('input');version.placeholder='分支版本待解析';version.disabled=true;version.setAttribute('aria-label',`${label} 版本`);
 const check=element('button','检查版本','check');check.type='button';check.hidden=true;
 const status=element('div','','status');status.setAttribute('role','status');valueBox.append(version,check,status);
 const item=state.dependencies[id]={mode:'default'};let generation=0;
 const invalidate=()=>{generation++;check.disabled=false;item.error=false;item.loading=false;status.className='status';status.textContent='';updateSummary();};
 item.readVersion=()=>version.value.trim();
 mode.addEventListener('change',()=>{item.mode=mode.value;version.value='';version.disabled=mode.value!=='custom';version.placeholder=mode.value==='default'?'分支版本待解析':mode.value==='latest'?'构建时解析':'输入版本号';check.hidden=mode.value!=='custom';invalidate();});
 version.addEventListener('input',invalidate);
 const queryVersion=async()=>{
  if(mode.value!=='custom')return;
  invalidate();const seq=generation;
  const typed=version.value.trim();if(!/^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$/.test(typed)){status.textContent='请输入有效版本号';item.error=true;updateSummary();return;}
  check.disabled=true;item.loading=true;updateSummary();status.textContent='查询中…';
  try{
   let source;
   for(const repo of dependencyRepositories){
    const url=`${repositories[repo]}/${group.replaceAll('.','/')}/${artifact}/maven-metadata.xml`;
    let response;
    try{response=await request(url);}catch(error){if(error.status===404)continue;throw error;}
    const xml=new DOMParser().parseFromString(await response.text(),'application/xml');
    if(xml.querySelector('parsererror')||xml.documentElement.nodeName!=='metadata')throw new Error('Maven 返回的 metadata 格式无效');
    const versions=[...xml.querySelectorAll('versioning > versions > version')].map(x=>x.textContent.trim());
    if(versions.includes(typed)){source=repo;break;}
   }
   if(seq!==generation)return;
   if(!source)throw new Error('仓库中未找到此版本');
   version.value=typed;status.className='status success';status.textContent=`✓ maven-${source}`;
  }catch(e){if(seq===generation){item.error=true;status.className='status error';status.textContent=e.message;}}
  finally{if(seq===generation){check.disabled=false;item.loading=false;updateSummary();}}
 };
 check.addEventListener('click',queryVersion);
 row.append(info,mode,valueBox);$('dependencies').append(row);
}
function setAllModes(mode){
 for(const select of document.querySelectorAll('.dependency select')){select.value=mode;select.dispatchEvent(new Event('change'));}
}
$('toggle-dependencies').addEventListener('click',()=>{
 const list=$('dependencies');
 list.hidden=!list.hidden;
 const button=$('toggle-dependencies');
 button.setAttribute('aria-expanded',String(!list.hidden));
 const label=list.hidden?'展开依赖':'收起依赖';
 button.setAttribute('aria-label',label);
 button.title=label;
});
$('reset-modes').addEventListener('click',()=>setAllModes('default'));
$('latest-modes').addEventListener('click',()=>setAllModes('latest'));
$('config-form').addEventListener('submit',e=>e.preventDefault());
$('config-form').addEventListener('input',updateSummary);

updateSummary();

let phase='idle',activeMode=null,activeTask=null,retryRequest=null,pollTimer=null,pollController=null,pageActive=true;
const taskLabels={pending:'等待运行',running:'运行中',succeeded:'已完成',failed:'构建失败'};
function setPhase(next,mode=activeMode){
 phase=next;activeMode=mode;
 const locked=phase!=='idle';
 $('config-form').inert=locked;
 $('config-form').setAttribute('aria-disabled',String(locked));
 for(const [buttonMode,label] of [['debug','Debug · Maven Local'],['publish','开始发布']]){
  const button=$(`${buttonMode}-run`);
  const active=buttonMode===mode;
  const loading=active&&['submitting','pending','running'].includes(phase);
  button.disabled=locked&&!(phase==='unconfirmed'&&active);
  button.classList.toggle('is-loading',loading);
  button.setAttribute('aria-busy',String(loading));
  button.textContent=active?({submitting:'正在提交…',unconfirmed:'重试提交',pending:'等待运行…',running:'运行中…',succeeded:'已完成'}[phase]||label):label;
 }
}
function currentRequest(mode){
 const dependencies=Object.fromEntries(Object.entries(state.dependencies).map(([key,item])=>[key,{mode:item.mode,version:item.readVersion()}]));
 return buildRequest(state.branches,dependencies,$('suffix').value,mode==='publish'?$('publish-url').value:undefined,{mode,platforms:selectedPlatforms(),debug_enabled:$('build-debug').checked,trace:$('build-trace').checked,ltpo:$('build-ltpo').checked});
}
function showTask(status,message,url){
 const link=element('a','查看构建任务');link.href=url;link.target='_blank';link.rel='noopener noreferrer';
 status.replaceChildren(document.createTextNode(message),link);
}
function stopPolling(){
 clearTimeout(pollTimer);pollTimer=null;
 pollController?.abort();pollController=null;
}
function showTaskState(task){
 const status=$(`${task.mode}-status`);
 status.classList.toggle('error',task.status==='failed');
 status.classList.toggle('success',task.status==='succeeded');
 showTask(status,`任务 #${task.id} ${taskLabels[task.status]}。`,task.url);
 if(task.status==='failed'){
  stopPolling();forgetFailedBuild(task.id);activeTask=null;setPhase('idle');
 }else{
  setPhase(task.status,task.mode);
  if(task.status==='succeeded')stopPolling();
 }
}
function schedulePoll(){
 clearTimeout(pollTimer);
 if(!pageActive||!activeTask||!['pending','running'].includes(activeTask.status))return;
 pollTimer=setTimeout(async()=>{
  pollTimer=null;
  const task=activeTask,controller=new AbortController();pollController=controller;
  try{
   const result=await getBuildStatus(task.id,{signal:controller.signal});
   if(!pageActive||activeTask!==task)return;
   task.status=result.status;showTaskState(task);
  }catch{
   if(!pageActive||activeTask!==task||controller.signal.aborted)return;
   showTask($(`${task.mode}-status`),`任务 #${task.id} ${taskLabels[task.status]}。暂时无法获取状态，正在重试…`,task.url);
  }finally{
   if(pollController===controller)pollController=null;
   schedulePoll();
  }
 },5000);
}
async function submit(mode){
 if(phase!=='idle'&&!(phase==='unconfirmed'&&mode===activeMode))return;
 setPhase('submitting',mode);
 const status=$(mode==='debug'?'debug-status':'publish-status');
 for(const id of ['debug-status','publish-status']){$(id).textContent='';$(id).classList.remove('error','success');}
 status.textContent='正在提交…';
 try{
  if(mode==='debug'){
   $('repository-error').hidden=true;
   for(const id of ['publish-url','maven-username','maven-password'])$(id).setAttribute('aria-invalid','false');
  }
  retryRequest??={request:currentRequest(mode),credentials:mode==='publish'?readCredentials():null};
  const result=await submitBuild(mode,retryRequest.request,retryRequest.credentials);
  retryRequest=null;
  if(mode==='publish')$('maven-password').value='';
  activeTask={...result,mode};showTaskState(activeTask);schedulePoll();
 }catch(error){
  status.classList.add('error');
  if(error.id)showTask(status,error.message,workflowURL());
  else status.textContent=error.message;
  if(!error.unconfirmed)retryRequest=null;
  if(error.id&&error.status==='failed')forgetFailedBuild(error.id);
  setPhase(error.unconfirmed?'unconfirmed':'idle',mode);
 }
}
$('debug-run').addEventListener('click',()=>submit('debug'));
$('publish-run').addEventListener('click',()=>submit('publish'));
window.addEventListener('pagehide',()=>{pageActive=false;stopPolling();});
window.addEventListener('pageshow',()=>{pageActive=true;if(!pollController)schedulePoll();});

function readCredentials(){
 const credentials={username:$('maven-username').value,password:$('maven-password').value};
 validateCredentials(credentials.username,credentials.password);
 return credentials;
}
for(const id of ['publish-url','maven-username','maven-password']){
 $(id).addEventListener('blur',event=>{
  if(!$(id).value.trim()||event.relatedTarget?.id==='debug-run'){
   $('repository-error').hidden=true;$(id).setAttribute('aria-invalid','false');return;
  }
  try{
   if(id==='publish-url')$(id).value=publicationURL($(id).value);
   else if(id==='maven-username')validateUsername($(id).value);
   else validatePassword($(id).value);
   $('repository-error').hidden=true;$(id).setAttribute('aria-invalid','false');
   updateSummary();
  }catch(error){$('repository-error').textContent=error.message;$('repository-error').hidden=false;$(id).setAttribute('aria-invalid','true');}
 });
}
