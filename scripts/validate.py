#!/usr/bin/env python3
import json
import os
import base64
import hashlib
import time
import subprocess
import sys
from contextlib import contextmanager
from urllib.parse import quote, urlsplit
from pathlib import Path
import re
import urllib.error
import urllib.request

REPOS={'core':'CPF-KMP-CMP/compose-multiplatform-core','cmp':'CPF-KMP-CMP/compose-multiplatform'}
DEPENDENCIES=('kotlin','skiko','coroutines_core','coroutines_cmp','serialization','atomicfu','datetime','okio')
PLATFORMS=('android','ohos','uikit','macos')
SUBMIT_URL='https://vjmzpjoqgjuvlaubmjgm.supabase.co/functions/v1/submit-build'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def http(url,method='GET',data=None,headers=None,allow_missing=False):
    # No automatic credential forwarding across redirects.
    request=urllib.request.Request(url,data=data,headers=headers or {},method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request,timeout=90) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if allow_missing and error.code==404:return None
        hint={401:'authentication rejected',403:'access denied',409:'conflict',429:'rate limited'}.get(error.code,'request failed')
        raise ValueError(f'HTTP {method} {error.code} ({hint}): '+urlsplit(url).path) from None
    except urllib.error.URLError:
        raise ValueError(f'Network request failed during {method}: '+urlsplit(url).path) from None
    except TimeoutError:
        raise ValueError(f'Network timeout during {method}: '+urlsplit(url).path) from None

_secrets=set()
_started=time.monotonic()

def redact(value):
    text=str(value)
    for secret in sorted(_secrets,key=len,reverse=True): text=text.replace(secret,'***')
    return text

def protect_secrets(*values):
    values=set(values)
    for value in sorted(values-_secrets,key=len,reverse=True):
        if value and os.environ.get('GITHUB_ACTIONS')=='true':
            # Runner control command only; credential jobs must not pipe stdout into artifacts.
            print('::add-mask::'+value.replace('%','%25').replace('\r','%0D').replace('\n','%0A'),flush=True)
    _secrets.update(value for value in values if value)

def protect_credentials(username,password):
    values={username,password,base64.b64encode((username+':'+password).encode()).decode()}
    for value in (username,password): values.update((quote(value,safe=''),json.dumps(value,ensure_ascii=True)[1:-1]))
    protect_secrets(*values)

def log(message):
    text=redact(message).replace('\r','\\r').replace('\n','\\n')
    print(f'[cmp +{time.monotonic()-_started:.1f}s] '+text,flush=True)

def error_message(error):
    if isinstance(error,subprocess.CalledProcessError):
        tool=Path(error.cmd[0]).name if isinstance(error.cmd,(list,tuple)) else 'command'
        return f'{tool} exited with code {error.returncode}; see command output above'
    if isinstance(error,json.JSONDecodeError): return f'Invalid JSON at line {error.lineno}, column {error.colno}'
    if isinstance(error,UnicodeError): return 'Invalid text encoding'
    if isinstance(error,KeyError): return 'Missing required configuration field or environment variable'
    if isinstance(error,urllib.error.HTTPError): return 'HTTP request failed: '+str(error.code)
    if isinstance(error,TimeoutError): return 'Network request timed out'
    if isinstance(error,OSError): return 'I/O failure: '+(os.strerror(error.errno) if error.errno else type(error).__name__)
    if isinstance(error,ValueError): return redact(str(error))
    location=error.__traceback__
    while location and location.tb_next: location=location.tb_next
    source=f' at {Path(location.tb_frame.f_code.co_filename).name}:{location.tb_lineno} ({location.tb_frame.f_code.co_name})' if location else ''
    return 'Unexpected '+type(error).__name__+source

def fail(error):
    log('ERROR: '+error_message(error))
    raise SystemExit(1)

@contextmanager
def stage(name):
    started=time.monotonic();log('START '+name)
    try: yield
    except BaseException as error:
        log(f'FAILED {name} after {time.monotonic()-started:.1f}s: '+error_message(error))
        raise
    else: log(f'DONE {name} in {time.monotonic()-started:.1f}s')

def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,127}',value):raise ValueError('Invalid dependency version')
    return value

def validate(data,debug=False):
    fields={'schema_version','sources','suffix','publish_url','dependencies','build_options','platforms'}
    if debug:fields.add('submission_nonce')
    if not isinstance(data,dict) or set(data)-fields:raise ValueError('Unknown request fields (credentials are not allowed)')
    if type(data.get('schema_version')) is not int or data.get('schema_version')!=1:raise ValueError('Unsupported schema version')
    nonce=data.get('submission_nonce')
    if 'submission_nonce' in data and (not isinstance(nonce,str) or not re.fullmatch(r'[0-9a-f]{32}',nonce)):raise ValueError('Invalid submission nonce')
    platforms=data.get('platforms',['android','ohos','uikit'])
    if not isinstance(platforms,list) or not platforms or any(not isinstance(value,str) or value not in PLATFORMS for value in platforms) or len(set(platforms))!=len(platforms):
        raise ValueError('Select one or more unique platforms: '+', '.join(PLATFORMS))
    platforms=[value for value in PLATFORMS if value in platforms]
    options=data.get('build_options',{})
    if not isinstance(options,dict) or set(options)-{'debug_enabled','trace','ltpo'} or any(type(value) is not bool for value in options.values()):raise ValueError('Build options must be boolean debug_enabled, trace and ltpo switches')
    suffix=data.get('suffix','')
    if not isinstance(suffix,str):raise ValueError('Invalid suffix')
    suffix=suffix.strip().lstrip('-')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',suffix) or not suffix[-1].isdigit() or suffix.startswith(('1.12.0','1.9.2')):raise ValueError('Suffix must end with a digit, max 64 characters, not a full version')
    sources=data.get('sources')
    if not isinstance(sources,dict) or set(sources)!=set(REPOS):raise ValueError('Both source repositories are required')
    for key,source in sources.items():
        if not isinstance(source,dict) or set(source)!={'repository','branch','sha'} or source['repository']!=REPOS[key]:raise ValueError('Only approved CPF source repositories are allowed')
        if not isinstance(source['sha'],str) or not re.fullmatch(r'[0-9a-f]{40}',source['sha']):raise ValueError('Source SHA must be pinned')
        branch=source['branch']
        if not isinstance(branch,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}',branch) or any(x in branch for x in ('..','//','@{')) or any(part.startswith('.') or part.endswith('.lock') for part in branch.split('/')) or branch.endswith(('/','.')):raise ValueError('Invalid branch name')
    dependencies=data.get('dependencies',{})
    if not isinstance(dependencies,dict) or set(dependencies)-set(DEPENDENCIES):raise ValueError('Unknown dependency')
    normalized={}
    for key in DEPENDENCIES:
        item=dependencies.get(key,{'mode':'default'})
        if not isinstance(item,dict) or set(item)-{'mode','version'} or item.get('mode') not in ('default','latest','custom'):raise ValueError('Invalid dependency mode')
        normalized[key]={'mode':item['mode']}
        if item['mode']=='custom':normalized[key]['version']=version(item.get('version'))
        elif item.get('version') is not None:raise ValueError('Only custom mode accepts a version')
    request=dict(schema_version=1,sources=sources,suffix=suffix,compose_version='1.12.0-'+suffix,dependencies=normalized,platforms=platforms,
                 build_options={key:'ohos' in platforms and options.get(key,False) for key in ('debug_enabled','trace','ltpo')})
    if not debug:request['publish_url']=publication_url(data.get('publish_url'))
    return request


def publication_url(value):
    if not isinstance(value,str) or len(value)>2048:
        raise ValueError('Maven URL must be an HTTPS URL')
    value=value.strip()
    # Keep the same deliberately limited URL grammar as the browser validator.
    match=re.fullmatch(r'https://([A-Za-z0-9.-]+)(?::([0-9]{1,5}))?(/[A-Za-z0-9._~/-]*)?',value)
    if not match:raise ValueError('Maven URL must use HTTPS without credentials, query or fragment')
    host,port,path=match.groups();path=path or '/'
    if len(host)>253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in host.split('.')):
        raise ValueError('Invalid Maven hostname')
    if port and not 1<=int(port)<=65535:raise ValueError('Invalid Maven port')
    if '//' in path or any(part in ('.','..') for part in path.split('/')):
        raise ValueError('Invalid Maven repository path')
    authority=host.lower()+(':'+str(int(port)) if port and int(port)!=443 else '')
    return 'https://'+authority+path.rstrip('/')+'/'


def decrypt_credentials(envelope_text, private_pem, request_json, now=None):
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not private_pem: raise ValueError('MAVEN_CREDENTIAL_PRIVATE_KEY is not configured')
    def decode(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',value): raise ValueError()
        raw=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
        if base64.urlsafe_b64encode(raw).decode().rstrip('=')!=value: raise ValueError()
        return raw
    try:
        if not isinstance(envelope_text,str) or len(envelope_text)>16384: raise ValueError()
        envelope=json.loads(envelope_text)
        if set(envelope)!={'v','key_id','iv','wrapped_key','ciphertext'} or type(envelope['v']) is not int or envelope['v']!=1: raise ValueError()
        key=serialization.load_pem_private_key(private_pem.encode(),password=None)
        public=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        if hashlib.sha256(public).hexdigest()!=envelope['key_id']: raise ValueError()
        aes=key.decrypt(decode(envelope['wrapped_key']),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
        iv=decode(envelope['iv']); ciphertext=decode(envelope['ciphertext'])
        if len(aes)!=32 or len(iv)!=12 or not 17<=len(ciphertext)<=8192: raise ValueError()
        payload=json.loads(AESGCM(aes).decrypt(iv,ciphertext,None))
    except Exception:
        raise ValueError('Encrypted credentials could not be verified') from None
    if not isinstance(payload,dict) or set(payload)!={'username','password','request_sha256','expires_at'}: raise ValueError('Invalid credential fields')
    if payload['request_sha256']!=hashlib.sha256(request_json.encode()).hexdigest(): raise ValueError('Credentials do not match this request')
    now=int(time.time()) if now is None else now
    if type(payload['expires_at']) is not int or not now<payload['expires_at']<=now+86400: raise ValueError('Credentials expired or validity window is invalid')
    username,password=payload['username'],payload['password']
    if not isinstance(username,str) or not 1<=len(username.encode('utf-16-le'))//2<=128 or username!=username.strip() or re.search(r'[:\x00-\x1f\x7f]',username): raise ValueError('Invalid Maven username format')
    if not isinstance(password,str) or not 1<=len(password.encode('utf-16-le'))//2<=1024 or re.search(r'[\x00-\x1f\x7f]',password): raise ValueError('Invalid Maven password format')
    return username,password,payload['expires_at']

def task_api(action,status=None):
    # GitHub 签发短期身份凭证；服务端据此确定仓库、工作流和当前 commit。
    token=os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']
    protect_secrets(token)
    oidc_url=os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    separator='&' if '?' in oidc_url else '?'
    identity=json.loads(http(oidc_url+separator+'audience=supabase-cmp-builder',
                             headers={'Authorization':'Bearer '+token}))
    jwt=identity.get('value')
    if not isinstance(jwt,str) or not jwt:raise ValueError('GitHub did not provide an OIDC token')
    protect_secrets(jwt)
    body={'action':action}
    if status is not None:body['status']=status
    result=json.loads(http(SUBMIT_URL,method='POST',data=json.dumps(body).encode(),
                           headers={'Authorization':'Bearer '+jwt,'Content-Type':'application/json'}))
    if not isinstance(result,dict) or not re.fullmatch(r'[1-9][0-9]*',str(result.get('id',''))):
        raise ValueError('Invalid task response')
    return result

def claim_task(debug=False):
    with stage('task claim'):
        task=task_api('claim')
        if type(task.get('accepted')) is not bool:raise ValueError('Task claim was not confirmed')
        if not task['accepted']:
            log('Task '+str(task['id'])+' already claimed or completed; skipping')
            with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('claimed=false\n')
            return None
        # 领取后即记录状态，让后续校验或构建失败也能由 finish 回写。
        with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('claimed=true\n')
        if not isinstance(task.get('request_json'),str) or (not debug and not isinstance(task.get('credentials'),dict)):
            raise ValueError('Task payload is incomplete')
        log('Task '+str(task['id'])+' loaded')
        return task

def finish_task():
    status=os.environ['TASK_RESULT']
    if status not in ('succeeded','failed'):raise ValueError('Invalid final task status')
    with stage('task completion'):
        result=task_api('finish',status)
        if result.get('status')!=status:raise ValueError('Final task status was not confirmed')
        log('Task '+str(result['id'])+': '+status)

if __name__=='__main__':
    try:
        if sys.argv[1:]==['--finish']:finish_task()
        else:raise ValueError('Unsupported arguments')
    except Exception as error: fail(error)
