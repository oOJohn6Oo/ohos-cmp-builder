export function publicationURL(value){
 if(typeof value!=='string'||value.length>2048)throw new Error('请输入有效的 Maven HTTPS 地址');
 const match=value.trim().match(/^https:\/\/([A-Za-z0-9.-]+)(?::([0-9]{1,5}))?(\/[A-Za-z0-9._~/-]*)?$/);
 if(!match)throw new Error('仓库地址须为 HTTPS，不能包含账号密码、查询参数或锚点');
 const [,host,port,rawPath]=match;const path=rawPath||'/';
 if(host.length>253||host.split('.').some(label=>! /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(label)))throw new Error('仓库域名格式不正确');
 if(port&&(+port<1||+port>65535))throw new Error('仓库端口须为 1–65535');
 if(path.includes('//')||path.split('/').some(p=>p==='.'||p==='..'))throw new Error('仓库路径格式不正确');
 return `https://${host.toLowerCase()}${port&&+port!==443?':'+Number(port):''}${path.replace(/\/+$/,'')}/`;
}
export function validateUsername(username){
 if(typeof username!=='string'||username.length<1||username.length>128||username!==username.trim()||/[:\x00-\x1f\x7f]/.test(username))throw new Error('账号须为 1–128 位，不能包含冒号、控制字符或首尾空格');
}
export function validatePassword(password){
 if(typeof password!=='string'||password.length<1||password.length>1024||/[\x00-\x1f\x7f]/.test(password))throw new Error('密码须为 1–1024 位，不能包含控制字符');
}
export function validateCredentials(username,password){
 validateUsername(username);validatePassword(password);
}
const encoder=new TextEncoder();
const base64url=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
const hex=bytes=>Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,'0')).join('');
export async function encryptCredentials(username,password,requestJSON,publicConfig,expiresAt){
 validateCredentials(username,password);
 const now=Math.floor(Date.now()/1000);
 if(!Number.isInteger(expiresAt)||expiresAt<=now||expiresAt>now+86400)throw new Error('提交已过期，请重新提交');
 if(!globalThis.crypto?.subtle)throw new Error('凭据加密需要 HTTPS 或 localhost');
 let der,key;
 try{
  const pem=publicConfig.public_key.replace(/-----BEGIN PUBLIC KEY-----|-----END PUBLIC KEY-----|\s/g,'');
  der=Uint8Array.from(atob(pem),c=>c.charCodeAt(0));
  if(hex(await crypto.subtle.digest('SHA-256',der))!==publicConfig.key_id)throw new Error();
  key=await crypto.subtle.importKey('spki',der,{name:'RSA-OAEP',hash:'SHA-256'},false,['encrypt']);
 }catch{throw new Error('发布公钥无效，请联系页面维护者');}
 const aes=await crypto.subtle.generateKey({name:'AES-GCM',length:256},true,['encrypt']);
 const raw=new Uint8Array(await crypto.subtle.exportKey('raw',aes));
 const iv=crypto.getRandomValues(new Uint8Array(12));
 const payload={username,password,request_sha256:hex(await crypto.subtle.digest('SHA-256',encoder.encode(requestJSON))),expires_at:expiresAt};
 const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv},aes,encoder.encode(JSON.stringify(payload)));
 const wrapped=await crypto.subtle.encrypt({name:'RSA-OAEP'},key,raw);raw.fill(0);
 return JSON.stringify({v:1,key_id:publicConfig.key_id,iv:base64url(iv),wrapped_key:base64url(wrapped),ciphertext:base64url(ciphertext)});
}
export async function loadPublicKey(fetcher=fetch){
 let response;
 try{response=await fetcher(new URL('./credential-public-key.json',import.meta.url),{cache:'no-store',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(15000)});}
 catch{throw new Error('无法读取发布公钥，请稍后重试');}
 if(!response.ok)throw new Error('发布公钥尚未配置');
 return response.json();
}
