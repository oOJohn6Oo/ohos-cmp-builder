import {publicationURL,validateCredentials,encryptCredentials,loadPublicKey} from './credential-crypto.js';
const REPOSITORY='oOJohn6Oo/ohos-cmp-builder';
const API='https://vjmzpjoqgjuvlaubmjgm.supabase.co/functions/v1/submit-build';
const STATUS_API='https://vjmzpjoqgjuvlaubmjgm.supabase.co/rest/v1/cmp_builds';
const PUBLISHABLE_KEY='sb_publishable_pSJV2qW4Y5YrvgXM8MioMA_FGLLOZly';
const attempts=new Map();
const taskStatuses=['pending','running','succeeded','failed'];
const validTaskID=id=>typeof id==='string'&&/^[1-9][0-9]*$/.test(id);
function unconfirmed(){const error=new Error('提交结果未确认，请重试；不会重复创建任务');error.unconfirmed=true;return error;}

export const normalizeSuffix=value=>value.trim().replace(/^-+/,'');
export const validSuffix=value=>/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value)&&/[0-9]$/.test(value)&&!/^1\.(12\.0|9\.2)/.test(value);

export function buildRequest(branches,dependencies,suffix,url,{mode='publish',platforms=['ohos'],debug_enabled=false,trace=false,ltpo=false}={}){
 if(!['debug','publish'].includes(mode))throw new Error('构建模式无效');
 if(!Array.isArray(platforms)||!platforms.length)throw new Error('请至少选择一个构建平台');
 const selectedPlatforms=['android','ohos','uikit','macos'].filter(value=>platforms.includes(value));
 if(selectedPlatforms.length!==platforms.length)throw new Error('构建平台无效');
 if(typeof debug_enabled!=='boolean'||typeof trace!=='boolean'||typeof ltpo!=='boolean')throw new Error('构建开关无效');
 const sources={};
 for(const [key,repository] of Object.entries({core:'CPF-KMP-CMP/compose-multiplatform-core',cmp:'CPF-KMP-CMP/compose-multiplatform'})){
  const b=branches[key];
  const label=key==='core'?'CMP Core':'CMP';
  if(!b?.name)throw new Error(`请选择 ${label} 分支`);
  if(!/^[0-9a-f]{40}$/.test(b.sha||''))throw new Error(`${label} 分支信息加载失败，请刷新分支列表后重新选择`);
  if(b.repository!==repository)throw new Error('仅支持 CPF 的两个源码仓库');
  sources[key]={repository,branch:b.name,sha:b.sha};
 }
 suffix=normalizeSuffix(suffix);
 if(!validSuffix(suffix))throw new Error('请填写有效后缀，并以数字结尾');
 const choices={};
 for(const [key,item] of Object.entries(dependencies)){
  if(!['default','latest','custom'].includes(item.mode))throw new Error('依赖模式无效');
  choices[key]={mode:item.mode};
  if(item.mode==='custom'){
   const v=(item.version||'').trim();
   if(!/^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$/.test(v))throw new Error(`请填写 ${key} 的有效版本`);
   choices[key].version=v;
  }
 }
 const ohos=selectedPlatforms.includes('ohos');
 const request={schema_version:1,sources,suffix,platforms:selectedPlatforms,dependencies:choices,build_options:{debug_enabled:ohos&&debug_enabled,trace:ohos&&trace,ltpo:ohos&&ltpo}};
 if(mode==='publish')request.publish_url=publicationURL(url);
 return request;
}

export function workflowURL(){
 return `https://github.com/${REPOSITORY}/actions/workflows/build.yml`;
}

export async function getBuildStatus(id,{fetcher=fetch,signal}={}){
 if(!validTaskID(id))throw new Error('任务 ID 无效');
 const timeout=AbortSignal.timeout(20000);
 const response=await fetcher(`${STATUS_API}?id=eq.${id}&select=id::text,status`,{
  headers:{apikey:PUBLISHABLE_KEY},
  credentials:'omit',cache:'no-store',redirect:'error',signal:signal?AbortSignal.any([signal,timeout]):timeout,
 });
 if(!response.ok)throw new Error(`暂时无法查询构建状态（HTTP ${response.status}）`);
 const rows=await response.json();
 const result=Array.isArray(rows)&&rows.length===1?rows[0]:null;
 if(result?.id!==id||!taskStatuses.includes(result.status))throw new Error('构建状态响应无效');
 return {id:result.id,status:result.status};
}

async function checkBuildAvailability(fetcher){
 let response;
 try{
  response=await fetcher(`${STATUS_API}?status=in.(pending,running)&select=id::text,status&limit=1`,{
   headers:{apikey:PUBLISHABLE_KEY},
   credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(20000),
  });
 }catch{throw new Error('暂时无法检查构建状态，请稍后重试');}
 if(!response.ok)throw new Error(`暂时无法检查构建状态（HTTP ${response.status}）`);
 let rows;
 try{rows=await response.json();}catch{throw new Error('构建状态响应无效');}
 if(!Array.isArray(rows)||rows.length>1||rows.some(row=>!validTaskID(row?.id)||!['pending','running'].includes(row.status)))throw new Error('构建状态响应无效');
 if(rows.length)throw new Error(`任务 #${rows[0].id} ${rows[0].status==='pending'?'正在等待运行':'正在运行'}，请稍后再试`);
}

export function forgetFailedBuild(id){
 for(const [key,attempt] of attempts)if(attempt.result?.id===id||attempt.failed?.id===id)attempts.delete(key);
}

export async function submitBuild(mode,request,credentials,{fetcher=fetch,publicConfig}={}){
 if(!['debug','publish'].includes(mode))throw new Error('构建模式无效');
 if(mode==='publish')validateCredentials(credentials.username,credentials.password);
 const serialized=JSON.stringify(request);
 // 同一页面的重试沿用同一次提交；Debug 不使用发布凭据。
 const identity=mode==='debug'?[mode,serialized]:[mode,serialized,credentials.username,credentials.password];
 const bytes=new TextEncoder().encode(JSON.stringify(identity));
 const key=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),byte=>byte.toString(16).padStart(2,'0')).join('');
 bytes.fill(0);
 const now=Math.floor(Date.now()/1000);
 for(const [key,attempt] of attempts)if(attempt.expiresAt<=now)attempts.delete(key);
 let attempt=attempts.get(key);
 if(!attempt){
  attempt={expiresAt:now+86400,serialized};
  if(mode==='debug'){
   const nonce=Array.from(crypto.getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join('');
   attempt.serialized=JSON.stringify({...request,submission_nonce:nonce});
  }
  attempts.set(key,attempt);
  // 同一次提交重试时复用加密包，服务端据此识别重复请求；不写入浏览器存储。
  attempt.encrypted=mode==='debug'?Promise.resolve(null):(async()=>JSON.parse(await encryptCredentials(credentials.username,credentials.password,attempt.serialized,publicConfig||await loadPublicKey(fetcher),attempt.expiresAt)))();
  attempt.encrypted.catch(()=>attempts.delete(key));
 }
 if(attempt.result)return {...attempt.result,reused:true};
 if(attempt.failed)throw attempt.failed;
 if(attempt.pending)return attempt.pending;
 attempt.pending=(async()=>{
  if(!attempt.sent)await checkBuildAvailability(fetcher);
  const encrypted=await attempt.encrypted;
  let response;
  try{
   attempt.sent=true;
   response=await fetcher(API,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mode,request_json:attempt.serialized,credentials:encrypted}),
    credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(60000),
   });
  }catch{throw unconfirmed();}
  let result;
  try{result=await response.json();}catch{throw unconfirmed();}
  if(!result||typeof result!=='object')throw unconfirmed();
  const id=validTaskID(result.id)?result.id:null;
  if(!response.ok){
   if(response.status===409&&!id)attempt.sent=false;
   const message=typeof result.error==='string'?result.error:`提交失败（HTTP ${response.status}）`;
   const error=new Error(id?`任务 #${id}：${message}`:message);
   error.id=id;error.status=result.status;
   if(id&&result.status==='failed')attempt.failed=error;
   else if(response.status>=500){error.unconfirmed=true;error.message+='；请重试确认，不会重复创建任务';}
   throw error;
  }
  if(!id||!taskStatuses.includes(result.status))throw unconfirmed();
  attempt.result={id,status:result.status,url:workflowURL()};
  return {...attempt.result,reused:false};
 })();
 try{return await attempt.pending;}finally{attempt.pending=null;}
}
