#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from types import SimpleNamespace
import urllib.request
from urllib.parse import urlsplit
from validate import REPOS, http, log, version, write_json, stage, fail, claim_task, validate, decrypt_credentials, protect_credentials
from publish import properties, versions, generate
from bump_versions import align_versions
from latest_dependencies import DEPENDENCIES, PUBLIC_MAVEN, latest_version

MAVEN=['https://maven.eazytec-cloud.com/nexus/repository/maven-inner/',PUBLIC_MAVEN]

BASE='https://maven.eazytec-cloud.com/nexus/repository/'
PUBLIC=BASE+'maven-public'
INNER=BASE+'maven-inner'


def masks(text):
    """Mask comments and strings while retaining offsets and newlines for brace scopes."""
    comments=list(text); code=list(text)
    token=re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27',re.S)
    for match in token.finditer(text):
        literal=match.group().startswith(('"',"'"))
        for i in range(match.start(),match.end()):
            if text[i] not in '\r\n':
                code[i]=' '
                if not literal: comments[i]=' '
    return ''.join(comments),''.join(code)


def scope_end(code,start):
    depth=0
    for i in range(start,len(code)):
        if code[i]=='{': depth+=1
        elif code[i]=='}':
            depth-=1
            if depth==0: return i
    raise ValueError('Unbalanced Gradle block')


def named_scope(code,name,start=0,end=None):
    end=len(code) if end is None else end
    matches=list(re.finditer(r'\b'+re.escape(name)+r'\s*\{',code[start:end]))
    if len(matches)!=1: raise ValueError('Expected exactly one '+name+' block')
    opening=start+matches[0].end()-1
    closing=scope_end(code,opening)
    if closing>=end: raise ValueError('Invalid '+name+' scope')
    return opening+1,closing


def parent_scope(code,position):
    stack=[]
    for i,c in enumerate(code[:position]):
        if c=='{': stack.append(i)
        elif c=='}':
            if not stack: raise ValueError('Unbalanced Gradle braces')
            stack.pop()
    return tuple(stack)


def configure(text,kind):
    comments,code=masks(text)
    start,end=0,len(text)
    if kind in ('cmp','plugins'):
        start,end=named_scope(code,'pluginManagement' if kind=='plugins' else 'dependencyResolutionManagement')
        start,end=named_scope(code,'repositories',start,end)
    region=comments[start:end]
    # The accepted forms deliberately exclude content filters, credentials and
    # computed URLs: changing those silently would alter repository semantics.
    url=r'[\x22\x27]'+re.escape(BASE)+r'maven-(?:inner|public)/?[\x22\x27]'
    receiver=r'handler\.maven' if kind=='core' else r'maven'
    block=receiver+r'[ \t]*\{\s*url\s*=\s*'+url+r'\s*;?\s*\}'
    call=receiver+r'[ \t]*\([ \t]*'+url+r'[ \t]*\)'
    expression=block if kind=='core' else '(?:'+block+'|'+call+')'
    pattern=re.compile(r'^[ \t]*'+expression+r'[ \t]*;?[ \t]*(?:\r?\n|$)',re.M)
    declarations=list(pattern.finditer(region))
    refs=list(re.finditer(re.escape(BASE)+r'maven-(?:inner|public)(?:/)?(?=[\x22\x27])',region))
    if not declarations or len(declarations)!=len(refs):
        raise ValueError(kind+': missing or unsupported Maven declaration; expected simple literal URLs')
    parents={parent_scope(code,start+m.start()+len(m.group())-len(m.group().lstrip())) for m in declarations}
    if len(parents)!=1: raise ValueError(kind+': repositories in different scopes; refusing to move them')
    public=[m for m in declarations if PUBLIC in m.group()]
    if not public: raise ValueError(kind+': public repository is required')
    anchor=public[0]
    original=text[start+anchor.start():start+anchor.end()]
    newline='\r\n' if '\r\n' in text else '\n'
    pair=original.replace(PUBLIC,INNER).rstrip('\r\n')+newline+original.rstrip('\r\n')+newline
    result=text
    for match in reversed(declarations):
        replacement=pair if match==anchor else ''
        result=result[:start+match.start()]+replacement+result[start+match.end():]
    return result


def apply(core,cmp,dry_run=False):
    files=[
        (Path(core)/'buildSrc/repos.gradle',('core',)),
        (Path(core)/'settings.gradle',('plugins',)),
        (Path(cmp)/'components/settings.gradle.kts',('plugins','cmp')),
        (Path(cmp)/'gradle-plugins/settings.gradle.kts',('plugins',)),
    ]
    changes=[]
    for path,kinds in files:
        if path.is_symlink(): raise ValueError('Refusing symlink: '+str(path))
        original=path.read_bytes().decode('utf-8')
        updated=original
        for kind in kinds: updated=configure(updated,kind)
        checked=updated
        for kind in kinds: checked=configure(checked,kind)
        if checked!=updated: raise ValueError('Idempotence validation failed')
        changes.append((path,original,updated))
    staged=[]
    try:
        for path,original,updated in changes:
            label=path.relative_to(core if path.is_relative_to(core) else cmp)
            log(str(label)+(': already inner → public' if original==updated else ': normalize inner → public'))
            if dry_run or original==updated: continue
            if path.read_bytes().decode('utf-8')!=original: raise ValueError('Source changed during preparation')
            fd,name=tempfile.mkstemp(prefix='.maven-repositories-',dir=path.parent)
            staged.append((name,path))
            with os.fdopen(fd,'wb') as target: target.write(updated.encode('utf-8'))
            os.chmod(name,path.stat().st_mode & 0o7777)
        for name,path in staged: os.replace(name,path)
    finally:
        for name,_ in staged:
            if os.path.exists(name): os.unlink(name)
    if dry_run: log('Dry run: no files changed.')


def run(*command):subprocess.run([str(c) for c in command],check=True)

def pom_exists(group,artifact,v,repositories=MAVEN):
    path=group.replace('.','/')+'/'+artifact+'/'+v+'/'+artifact+'-'+v+'.pom'
    return any(http(base+path,method='HEAD',allow_missing=True) is not None for base in repositories)

def configure_publication_dependencies(cmp):
    root=Path(cmp);path=root/'components/settings.gradle.kts'
    if any(item.is_symlink() for item in (root,path.parent,path)) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Unsafe CMP publication repository configuration path')
    original=path.read_bytes().decode('utf-8');text=original
    newline='\r\n' if '\r\n' in text else '\n'
    repository='\n'.join((
        '        maven {',
        '            name = "CmpBuildPublication"',
        '            setUrl(System.getenv("COMPOSE_REPO_URL"))',
        '            credentials {',
        '                username = System.getenv("COMPOSE_REPO_USERNAME")',
        '                password = System.getenv("COMPOSE_REPO_KEY")',
        '            }',
        '            content {',
        '                includeGroupByRegex("org[.]jetbrains[.](compose|androidx)([.].*)?")',
        '            }',
        '        }',
    )).replace('\n',newline)
    for scope in ('pluginManagement','dependencyResolutionManagement'):
        comments,code=masks(text)
        start,end=named_scope(code,scope)
        start,end=named_scope(code,'repositories',start,end)
        names=re.findall(r'\bname\s*=\s*"CmpBuildPublication"',comments[start:end])
        if names:
            if len(names)!=1 or repository not in text[start:end]:
                raise ValueError('Unexpected existing CmpBuildPublication repository in '+scope)
            continue
        text=text[:start]+newline+repository+newline+text[start:]
    if text!=original:path.write_bytes(text.encode('utf-8'))
    log('CMP publication repository enabled for Compose and AndroidX dependencies')

def configure_gradle(core,cmp):
    for root,relative in (
        (core,'gradle/wrapper/gradle-wrapper.properties'),
        (cmp,'components/gradle/wrapper/gradle-wrapper.properties'),
        (cmp,'gradle-plugins/gradle/wrapper/gradle-wrapper.properties'),
    ):
        path=root/relative;label=root.name+'/'+relative
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()): raise ValueError('Unsafe Gradle Wrapper path: '+label)
        text=path.read_bytes().decode('utf-8')
        matches=list(re.finditer(r'^[ \t]*distributionUrl[ \t]*=[ \t]*(\S+)[ \t]*(?=\r?$)',text,re.M))
        if len(matches)!=1: raise ValueError('Expected one Gradle distributionUrl in '+label)
        match=matches[0]
        archive=Path(urlsplit(match.group(1).replace('\\:',':')).path).name
        if not re.fullmatch(r'gradle-[0-9][0-9A-Za-z.+-]*-(?:bin|all)\.zip',archive): raise ValueError('Unsupported Gradle distribution in '+label)
        updated=text[:match.start(1)]+'https\\://services.gradle.org/distributions/'+archive+text[match.end(1):]
        if updated!=text: path.write_bytes(updated.encode('utf-8'))
        log('Gradle: '+label+' → official '+archive)

def prepare(request,workspace):
    for key in ('core','cmp'):
        source=request['sources'][key];destination=workspace/key
        if destination.exists():raise ValueError('Source directory already exists: '+key)
        log('Fetching '+key+' @ '+source['sha'])
        run('git','init','-q',destination)
        run('git','-C',destination,'remote','add','origin','https://gitcode.com/'+REPOS[key]+'.git')
        run('git','-C',destination,'-c','credential.helper=','fetch','--quiet','--depth=1','origin',source['sha'])
        run('git','-C',destination,'checkout','--quiet','--detach','FETCH_HEAD')
        actual=subprocess.check_output(['git','-C',str(destination),'rev-parse','HEAD'],text=True).strip()
        if actual!=source['sha']:raise ValueError('Fetched SHA mismatch')
        log(key+' source ready; branch='+source['branch']+'; SHA='+actual)
    core=workspace/'core';cmp=workspace/'cmp'
    with stage('Gradle distribution configuration'): configure_gradle(core,cmp)
    with stage('Maven repository configuration'): apply(core,cmp)
    cv=versions(core/'gradle/libs.versions.toml');mv=versions(cmp/'components/gradle/libs.versions.toml')
    defaults={'kotlin':cv['kotlin'],'skiko':cv['skikoOhos'],'coroutines_core':cv['kotlinCoroutines'],'coroutines_cmp':mv['kotlinx-coroutines'],'serialization':cv['kotlinSerialization'],'atomicfu':cv['atomicFu'],'datetime':cv['datetime'],'okio':cv['okio']}
    if request['dependencies']['kotlin']['mode']=='default':
        values=[defaults['kotlin'],properties(cmp/'components/gradle.properties')['kotlin.version'],versions(cmp/'gradle-plugins/gradle/libs.versions.toml')['kotlin']]
        if len(set(values))!=1:raise ValueError('Kotlin branch defaults differ; select an explicit version')
    if request['dependencies']['skiko']['mode']=='default' and defaults['skiko']!=mv['ohosSkiko']:raise ValueError('Skiko branch defaults differ')
    resolved={}
    for key,info in DEPENDENCIES.items():
        choice=request['dependencies'][key];mode=choice['mode']
        if mode!='latest':
            resolved[key]=version(defaults[key] if mode=='default' else choice['version'])
            log(key+' = '+resolved[key]+'; mode='+mode)
            continue
        log('Resolving '+key+'; mode='+mode+'; branch version='+defaults[key])
        v=version(latest_version(info['group'],info['artifact'],defaults[key],key))
        for artifact in [info['artifact']]+info['checks']:
            if not pom_exists(info['group'],artifact,v,(PUBLIC_MAVEN,)):raise ValueError('Missing required Maven artifact: '+artifact+':'+v)
        resolved[key]=v;log(key+' = '+v+'; '+str(1+len(info['checks']))+' required Maven coordinates verified')
    with stage('dependency and publication versions'): align_versions(core,cmp,request['suffix'],resolved)
    # Recheck the native library on every release; let CMake decide what to rebuild.
    native=core/'compose/ui/ui-arkui/build.gradle.kts';text=native.read_text()
    if 'outputs.upToDateWhen { false }' not in text:
        if text.count('outputs.file(binaryFile)')!=1:raise ValueError('Cannot locate native build output declaration')
        text=text.replace('outputs.file(binaryFile)','outputs.file(binaryFile)\n            outputs.upToDateWhen { false }')
        native.write_text(text)
    log('Source configuration prepared; versions fixed for this build')

# Public toolchain mirror used by SDL's HarmonyOS CI.
# https://github.com/libsdl-org/SDL/blob/c314de7ce9a48c4b3a2ead40ddb53153c02c1ec6/.github/actions/setup-harmonyos-toolchain/action.yml
TOOLS_VERSION = '6.1.1.280'
TOOLS_URL = 'https://libsdl.org/harmonyos-devtools/commandline-tools-mac-arm64-6.1.1.280.zip'
TOOLS_SIZE = 2025336267

def install_harmony():
    if platform.system()!='Darwin' or platform.machine() not in ('arm64','aarch64'):
        raise ValueError('HarmonyOS build requires a macOS ARM64 runner')
    root=Path(os.environ['RUNNER_TEMP'])/'harmony-tools'
    marker=root/'.ready';unpacked=root/'unpacked'
    if root.is_symlink():raise ValueError('Unsafe HarmonyOS toolchain directory')
    cached=marker.is_file() and marker.read_text().strip()==TOOLS_URL and unpacked.is_dir()
    if cached:
        log('Reusing cached HarmonyOS Command Line Tools '+TOOLS_VERSION)
    else:
        if root.exists():shutil.rmtree(root)
        root.mkdir()
        archive=root/'tools.zip';digest=hashlib.sha256()
        log('Downloading HarmonyOS Command Line Tools '+TOOLS_VERSION+' (ARM64, SDL mirror)')
        received=0;last_progress=time.monotonic()
        with urllib.request.urlopen(TOOLS_URL,timeout=90) as response,archive.open('wb') as out:
            if urlsplit(response.url).scheme!='https':raise ValueError('Insecure download redirect')
            for chunk in iter(lambda:response.read(1024*1024),b''):
                digest.update(chunk);out.write(chunk);received+=len(chunk)
                if time.monotonic()-last_progress>=15:
                    log(f'Toolchain download: {received/1048576:.0f}/{TOOLS_SIZE/1048576:.0f} MiB ({received*100/TOOLS_SIZE:.1f}%)')
                    last_progress=time.monotonic()
        if archive.stat().st_size!=TOOLS_SIZE:raise ValueError('Incomplete or changed HarmonyOS toolchain archive')
        log(f'Toolchain download complete: {received} bytes; extracting archive')
        subprocess.run(['ditto','-xk',str(archive),str(unpacked)],check=True)
        log('Downloaded archive SHA-256 '+digest.hexdigest())
        archive.unlink()
    marker.unlink(missing_ok=True)
    log('Checking SDK layout and executables')
    candidates=[p for p in unpacked.rglob('oh-uni-package.json') if p.parent.name=='native' and p.parent.parent.name=='openharmony']
    if len(candidates)!=1:raise ValueError('Command Line Tools must include the OpenHarmony native SDK')
    native=candidates[0];api=int(json.loads(native.read_text())['apiVersion'])
    if api<20:raise ValueError('Current source requires OHOS SDK API >= 20')
    ohos=native.parent.parent
    if ohos.parent.name!='default':raise ValueError('Unsupported SDK layout; expected sdk/default/openharmony')
    sdk=ohos.parent.parent
    for relative in ('native/build-tools/cmake/bin/cmake','native/build-tools/cmake/bin/ninja','native/llvm/bin/ld.lld','native/build/cmake/ohos.toolchain.cmake'):
        if not (ohos/relative).is_file():raise ValueError('SDK missing '+relative)
    subprocess.run([str(ohos/'native/build-tools/cmake/bin/cmake'),'--version'],check=True)
    tools=sdk.parent
    hvigor=tools/'bin/hvigorw';ohpm=tools/'bin/ohpm';node=tools/'tool/node/bin/node'
    for file in (hvigor,ohpm,node):
        if not file.is_file():raise ValueError('Command Line Tools missing '+str(file.relative_to(tools)))
    for file in [hvigor,ohpm,node]:file.chmod(file.stat().st_mode|0o111)
    values={'OHOS_SDK_HOME':str(ohos),'DEVECO_SDK_HOME':str(sdk),'NODE_HOME':str(node.parent.parent),'HVIGORW':str(hvigor),'OHPM':str(ohpm)}
    os.environ.update(values)
    os.environ['PATH']=str(node.parent)+os.pathsep+os.environ['PATH']
    log('Runner architecture: '+platform.machine())
    subprocess.run([str(node),'--version'],check=True)
    marker.write_text(TOOLS_URL+'\n')
    log('HarmonyOS SDK API '+str(api)+' ready')

def build_har(core, output, release):
    cpp=core/'compose/ui/ui-arkui/src/ohosArm64Main/cpp'
    source=cpp/'compose/build/default/outputs/default/compose.har'
    output.mkdir(parents=True,exist_ok=True);target=output/('compose-'+release+'.har')
    if target.exists(): raise ValueError('HAR destination already exists')
    source.unlink(missing_ok=True)
    log('HAR: installing package dependencies')
    subprocess.run([os.environ['OHPM'],'install','--all'],cwd=cpp/'composeApp',check=True)
    log('HAR: assembling release package for both architectures')
    subprocess.run([os.environ['HVIGORW'],'--mode','module','-p','product=default','-p','module=compose@default','-p','buildMode=release','assembleHar','--no-daemon'],cwd=cpp/'composeApp',check=True)
    with tarfile.open(source,'r:*') as archive:
        files={m.name.removeprefix('./'):m for m in archive.getmembers() if m.isfile()}
        for name in ('package/oh-package.json5','package/Index.d.ets','package/ets/modules.abc','package/libs/arm64-v8a/libskikobridge.so','package/libs/x86_64/libskikobridge.so'):
            if name not in files or files[name].size==0: raise ValueError('HAR missing '+name)
        manifest=files['package/oh-package.json5']
        if manifest.size>1024*1024 or json.load(archive.extractfile(manifest))['version']!=release: raise ValueError('HAR manifest or version mismatch')
    shutil.copyfile(source,target)
    log('HAR verified: '+target.name+'; bytes='+str(target.stat().st_size)+'; version='+release)


def compile_sources(workspace, request, debug, credentials=None):
    release=request['compose_version'];build_options=request['build_options']
    if not debug and not credentials: raise ValueError('Publication credentials are required')
    for key in ('JAVA_HOME','ANDROID_HOME','OHOS_SDK_HOME'):
        if not os.environ.get(key): raise ValueError('Missing '+key)
    home=workspace/'gradle-home';home.mkdir(exist_ok=True)
    child_env=os.environ.copy()
    for key in ('MAVEN_USERNAME','MAVEN_PASSWORD','MAVEN_CREDENTIAL_PRIVATE_KEY','CREDENTIAL_ENVELOPE','REQUEST_JSON','COMPOSE_CUSTOM_VERSION','COMPOSE_GRADLE_PLUGIN_VERSION','COMPOSE_REPO_USERNAME','COMPOSE_REPO_KEY','COMPOSE_REPO_URL'):
        child_env.pop(key,None)
    child_env['GRADLE_USER_HOME']=str(home)
    core_task=':mpp:publishComposeJbToMavenLocal' if debug else ':mpp:publishComposeJb'
    core_command=['./gradlew',core_task,'-Pcompose.platforms=android,ohos,uikit','-Pjetbrains.publication.version.COMPOSE='+release,'--no-configuration-cache','--console=plain']
    for option,property_name in [('trace','androidx.ohosTraceEnabled'),('ltpo','androidx.ohosLtpoDiagnostics')]:
        enabled=build_options[option]
        if type(enabled) is not bool: raise ValueError('Build options must be booleans')
        core_command.append('-P'+property_name+'='+str(enabled).lower())
    record=None
    if debug:
        maven_dir=workspace/'maven';maven_dir.mkdir(exist_ok=False)
        child_env['JAVA_TOOL_OPTIONS']='-Dmaven.repo.local='+str(maven_dir)
        log('Maven Local: '+str(maven_dir))
    else:
        username,password=credentials
        protect_credentials(username,password)
        destination=request['publish_url']
        core_command.extend(['-Pmaven.remote.url='+destination,'-Pmaven.remote.username='+username,'-Pmaven.remote.password='+password])
        child_env.update(COMPOSE_REPO_URL=destination,COMPOSE_REPO_USERNAME=username,COMPOSE_REPO_KEY=password)
        configure_publication_dependencies(workspace/'cmp')
        record=dict(compose_version=release,maven_url=destination,components={'core':'not_started','cmp':'not_started'})
        log('Target Maven repository: '+destination)
    cmp_command=['bash','scripts/build-ohos.sh','--local' if debug else '--remote','--skip-html','-v',release,'--no-configuration-cache']
    for owner,command in (('core',core_command),('cmp',cmp_command)):
        if record:
            record['components'][owner]='failed'
        try:
            with stage(owner+' '+('local' if debug else 'remote')+' publication'):
                log('Task: '+core_task if owner=='core' else 'CMP: build-ohos.sh '+(' --local' if debug else ' --remote')+' --skip-html')
                log('Trace='+str(build_options['trace']).lower()+'; LTPO='+str(build_options['ltpo']).lower())
                subprocess.run(command,cwd=workspace/owner,env=child_env,check=True)
            if record:record['components'][owner]='succeeded'
        finally:
            if record:
                record['completed_at']=datetime.now(timezone.utc).isoformat()
                write_json(workspace/'publication.json',record)
    child_env.clear()
    core_command.clear()
    with stage('HAR compilation'): build_har(workspace/'core',workspace/'har',release)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workspace',default='work')
    parser.add_argument('--debug',action='store_true',default=os.environ.get('DEBUG_RUN')=='true')
    parser.add_argument('--report-only',action='store_true')
    args=parser.parse_args();workspace=Path(args.workspace).resolve();workspace.mkdir(parents=True,exist_ok=True)
    if args.report_only:
        request=json.loads((workspace/'request.json').read_text())
        with stage('release information'):
            outputs=generate(SimpleNamespace(core=str(workspace/'core'),cmp=str(workspace/'cmp'),har=str(workspace/'har'/('compose-'+request['compose_version']+'.har')),output_dir=str(workspace/'release-info'),har_url=os.environ['HAR_URL'],run_url=os.environ.get('RUN_URL'),core_branch=request['sources']['core']['branch'],cmp_branch=request['sources']['cmp']['branch'],publication_record=None if args.debug else str(workspace/'publication.json'),local_only=args.debug,build_options=request['build_options']))
        for output in outputs: log('Release information saved: '+output.name)
        return
    private=os.environ.pop('MAVEN_CREDENTIAL_PRIVATE_KEY','')
    for key in ('CREDENTIAL_ENVELOPE','REQUEST_JSON'):os.environ.pop(key,None)
    task=claim_task(debug=args.debug)
    if task is None:return
    with stage('input validation'):
        request=validate(json.loads(task['request_json']),debug=args.debug)
    credentials=None
    if not args.debug:
        with stage('credential validation'):
            username,password,_=decrypt_credentials(json.dumps(task['credentials']),private,task['request_json'])
            protect_credentials(username,password)
            credentials=(username,password)
    del task,private
    write_json(workspace/'request.json',request)
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('version='+request['compose_version']+'\n')
    log('Version='+request['compose_version']+'; mode='+('debug (Maven Local)' if args.debug else 'publish'))
    with stage('source preparation'): prepare(request,workspace)
    with stage('HarmonyOS environment'): install_harmony()
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('harmony_ready=true\n')
    compile_sources(workspace,request,args.debug,credentials)
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('built=true\n')
    if args.debug: log('Debug complete: Core and CMP published to Maven Local; HAR built; remote publication skipped')

if __name__=='__main__':
    try: main()
    except Exception as error: fail(error)
