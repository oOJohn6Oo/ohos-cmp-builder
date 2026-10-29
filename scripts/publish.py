from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

PRODUCTS = [
 ('CMP Gradle 插件', 'org.jetbrains.compose（插件 ID）', 'COMPOSE'),
 ('Runtime', 'org.jetbrains.compose.runtime:runtime*（不含 runtime-annotation）', 'COMPOSE'),
 ('Runtime Annotation', 'org.jetbrains.compose.runtime:runtime-annotation', 'RUNTIME_ANNOTATION'),
 ('UI', 'org.jetbrains.compose.ui:ui*', 'COMPOSE'),
 ('Foundation', 'org.jetbrains.compose.foundation:foundation*', 'COMPOSE'),
 ('Animation', 'org.jetbrains.compose.animation:animation*', 'COMPOSE'),
 ('Material', 'org.jetbrains.compose.material:material*（不含独立发布的 icons）', 'COMPOSE'),
 ('Export', 'org.jetbrains.compose.export:export', 'COMPOSE'),
 ('Material3', 'org.jetbrains.compose.material3:material3*', 'COMPOSE_MATERIAL3'),
 ('Material3 Adaptive', 'org.jetbrains.compose.material3.adaptive:adaptive*', 'COMPOSE_MATERIAL3_ADAPTIVE'),
 ('Lifecycle', 'org.jetbrains.androidx.lifecycle:lifecycle-*', 'LIFECYCLE'),
 ('SavedState', 'org.jetbrains.androidx.savedstate:savedstate*', 'SAVEDSTATE'),
 ('Window', 'org.jetbrains.androidx.window:window-core', 'WINDOW'),
 ('Annotation', 'org.jetbrains.compose.annotation-internal:annotation', 'ANNOTATION'),
 ('Collection', 'org.jetbrains.compose.collection-internal:collection', 'COLLECTION'),
 ('Graphics Shapes', 'org.jetbrains.androidx.graphics:graphics-shapes', 'GRAPHICS'),
 ('Navigation', 'org.jetbrains.androidx.navigation:navigation-*', 'NAVIGATION'),
 ('Navigation3', 'org.jetbrains.androidx.navigation3:navigation3-*', 'NAVIGATION_3'),
 ('Navigation Event', 'org.jetbrains.androidx.navigationevent:navigationevent*', 'NAVIGATION_EVENT'),
 ('Resources', 'org.jetbrains.compose.components:components-resources', 'COMPOSE'),
 ('UI Tooling Preview', 'org.jetbrains.compose.components:components-ui-tooling-preview', 'COMPOSE'),
]

def cell(value):
    return str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','&#124;').replace('`','&#96;').replace('\r',' ').replace('\n',' ')

def url(value):
    if not value: return None
    parsed=urlsplit(value)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or re.search(r'[\s<>"()]', value):
        raise ValueError('Links must be HTTPS URLs without credentials, query strings or fragments. Use a stable artifact page, not a signed URL.')
    return value

def properties(path):
    result={}
    for line in path.read_text().splitlines():
        if line.startswith(('kotlin.version=', 'compose.version=', 'deploy.version=', 'jetbrains.publication.version.')):
            key,value=line.split('=',1)
            if key in result: raise ValueError('Duplicate version property: '+key)
            result[key]=value.strip()
    return result

def versions(path):
    text=path.read_text()
    match=re.search(r'^\[versions\]\s*\n(.*?)(?=^\[|\Z)',text,re.M|re.S)
    if not match: raise ValueError('Missing [versions]: '+str(path))
    result={}
    for key,value in re.findall(r'^([A-Za-z0-9_.-]+)\s*=\s*"([^"\n]+)"',match.group(1),re.M):
        if key in result: raise ValueError('Duplicate catalog version: '+key)
        result[key]=value
    okio=re.search(r'^okio\s*=\s*\{[^\n]*?\bversion\s*=\s*"([^"\n]+)"',text,re.M)
    if okio: result['okio']=okio.group(1)
    return result

def git(root, *args):
    return subprocess.check_output(['git','-C',str(root),*args], text=True, stderr=subprocess.DEVNULL).strip()

def baseline(root, branch):
    return dict(branch=branch or git(root,'rev-parse','--abbrev-ref','HEAD'),commit=git(root,'rev-parse','HEAD'),
                subject=git(root,'show','-s','--format=%s','HEAD'),author=git(root,'show','-s','--format=%an','HEAD'),
                date=git(root,'show','-s','--format=%cI','HEAD'),working_tree_modified=bool(git(root,'status','--porcelain')))

def inspect_har(path):
    with tarfile.open(path,'r:*') as archive:
        manifests=[m for m in archive.getmembers() if m.name.removeprefix('./')=='package/oh-package.json5']
        if len(manifests)!=1 or not manifests[0].isfile() or manifests[0].size > 1024*1024:
            raise ValueError('HAR must contain exactly one regular package/oh-package.json5 (max 1 MiB)')
        manifest=json.load(archive.extractfile(manifests[0]))
    digest=hashlib.sha256(); md5=hashlib.md5()
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''): digest.update(chunk); md5.update(chunk)
    return dict(file=path.name,package_version=manifest['version'],bytes=path.stat().st_size,sha256=digest.hexdigest(),md5=md5.hexdigest())

def generate(args):
    core=Path(args.core); cmp=Path(args.cmp)
    gp=properties(core/'gradle.properties'); cp=properties(cmp/'components/gradle.properties'); pp=properties(cmp/'gradle-plugins/gradle.properties')
    cv=versions(core/'gradle/libs.versions.toml'); mv=versions(cmp/'components/gradle/libs.versions.toml'); pv=versions(cmp/'gradle-plugins/gradle/libs.versions.toml')
    release=gp['jetbrains.publication.version.COMPOSE']
    if not re.fullmatch(r'1\.12\.0-[A-Za-z0-9][A-Za-z0-9._-]*',release): raise ValueError('Expected a suffixed 1.12.0 Compose version')
    for version in [cp['compose.version'],cp['deploy.version'],pp['compose.version'],pp['deploy.version'],mv['compose']]:
        if version!=release: raise ValueError('Core/CMP publication versions differ')
    if len({cv['kotlin'],cv['composeCompilerPlugin'],cp['kotlin.version'],pv['kotlin']})!=1: raise ValueError('Kotlin / Compose Compiler versions differ')
    if cv['skikoOhos'] != mv['ohosSkiko']: raise ValueError('Core/CMP OHOS Skiko versions differ')
    artifact=None
    if not args.local_only and 'ohos' in args.platforms:
        if not args.har: raise ValueError('OHOS builds require a HAR artifact')
        artifact=inspect_har(Path(args.har))
        if artifact['package_version']!=release: raise ValueError('HAR package version does not match source configuration')
        artifact['download_url']=url(args.har_url)
    products=[dict(name=name,coordinate=coord,version=gp['jetbrains.publication.version.'+key]) for name,coord,key in PRODUCTS]
    deps=[
      ('Kotlin（Core / CMP 插件 / CMP components）',cv['kotlin']),('Compose Compiler',cv['composeCompilerPlugin']),
      ('Skiko（OHOS）',cv['skikoOhos']),('Skiko（通用）',cv['skiko']),
      ('kotlinx-coroutines（Core）',cv['kotlinCoroutines']),('kotlinx-coroutines（CMP）',mv['kotlinx-coroutines']),
      ('kotlinx-serialization',cv['kotlinSerialization']),('AtomicFU',cv['atomicFu']),('kotlinx-datetime',cv['datetime']),('Okio',cv['okio']),
    ]
    status='已发布到 Maven Local' if args.local_only else '未发布'
    publication=None
    if args.publication_record:
        record=json.loads(Path(args.publication_record).read_text())
        publication={key:record[key] for key in ('compose_version','components','maven_url','completed_at')}
        if publication.get('compose_version')!=release: raise ValueError('Publication record version differs')
        if not isinstance(publication.get('components'),dict): raise ValueError('Missing component publication states')
        components=publication['components']
        if set(components)!={'core','cmp'} or not all(v in ('succeeded','failed','not_started') for v in components.values()): raise ValueError('Invalid publication states')
        status='已发布' if all(v=='succeeded' for v in components.values()) else '发布未完成'
        url(publication['maven_url'])
        if not publication['maven_url']: raise ValueError('Missing Maven destination')
        completed=datetime.fromisoformat(publication['completed_at'].replace('Z','+00:00'))
        if completed.tzinfo is None: raise ValueError('Publication time must include a timezone')
    now=datetime.now(ZoneInfo('Asia/Shanghai'))
    sources={'CMP-Core':baseline(core,args.core_branch),'CMP':baseline(cmp,args.cmp_branch)}
    report=dict(schema_version=1,compose_version=release,status=status,generated_at=now.isoformat(),publication=publication,
                dependencies=dict(deps),products=products,har=artifact,sources=sources,run_url=url(args.run_url),local_maven_url=url(args.local_maven_url) if args.local_only else None,build_options=args.build_options,platforms=args.platforms)
    return write_report(report,args.output_dir)

def write_report(report,output_dir):
    now=datetime.fromisoformat(report['generated_at']).astimezone(ZoneInfo('Asia/Shanghai'))
    release=report['compose_version'];status=report['status'];publication=report['publication']
    artifact=report['har'];sources=report['sources'];products=report['products'];deps=report['dependencies'].items()
    url(report.get('run_url'))
    if artifact:url(artifact.get('download_url'))
    if publication:url(publication['maven_url'])
    local_maven_url=url(report.get('local_maven_url'))
    title='CMP-'+release+'-发布信息'
    lines=['# '+title,'',f'- Compose 版本：`{cell(release)}`',f'- 发布状态：{status}',f'- 报告生成时间：{now.strftime("%Y-%m-%d %H:%M:%S")}（北京时间）']
    platform_names={'android':'Android','ohos':'OHOS','uikit':'UIKit','macos':'macOS'}
    lines.append('- 构建平台：'+'、'.join(platform_names[name] for name in report['platforms']))
    for name,key in [('Debug 日志','debug_enabled'),('Trace','trace'),('LTPO','ltpo')]:
        lines.append('- '+name+'：'+('开启' if report['build_options'][key] else '关闭'))
    if status=='已发布到 Maven Local':
        lines += ['- Maven 仓库：Runner 本地 Maven（任务结束后释放）']
        if local_maven_url:lines += [f'- 本地产物：[CMP-{release}-maven-local.zip]({local_maven_url})（包含完整的 `org/` 目录）']
    if publication:
        lines += [f'- Maven 仓库：{publication["maven_url"]}',f'- 发布流程记录时间：{cell(publication["completed_at"])}',f'- 阶段结果：Core `{publication["components"]["core"]}` / CMP `{publication["components"]["cmp"]}`']
    if report['run_url']: lines.append(f'- 构建记录：[查看 GitHub Actions]({report["run_url"]})')
    lines += ['', '## 构建依赖','', '| 依赖 | 本次版本 |','| --- | --- |']
    lines += [f'| {cell(name)} | `{cell(version)}` |' for name,version in deps]
    lines += ['', '## 组件版本', '', '本表列出组件版本配置；平台产物按本次选择及源码发布任务确定。', '', '| 库 | Maven 坐标 / 插件 ID | 本次版本 |','| --- | --- | --- |']
    lines += [f'| {cell(p["name"])} | {cell(p["coordinate"])} | `{cell(p["version"])}` |' for p in products]
    if status=='已发布到 Maven Local': lines += ['', '> 本次仅发布到 Maven Local，未上传远端 Maven 仓库。']
    elif status!='已发布': lines += ['', '> 这是源码配置中的产物版本清单，不表示所有 Maven 产物已上传成功。']
    if artifact:
        lines += ['', '## HAR','',f'- 文件：`{cell(artifact["file"])}`',f'- 包内版本：`{cell(artifact["package_version"])}`',f'- 大小：{artifact["bytes"]:,} 字节',f'- MD5：`{artifact["md5"]}`',f'- SHA-256：`{artifact["sha256"]}`']
        lines += [f'- 下载入口：[下载 HAR / 查看构建产物]({artifact["download_url"]})' if artifact['download_url'] else '- 下载入口：尚未上传，当前仅有本地文件。']
        lines += ['', '此 HAR 与上述 Core、CMP 版本配套使用。下载入口可能需要仓库访问权限；产物保留期由上传配置决定。']
    lines += ['', '## 代码基线','', '| 仓库 | 分支 | Commit | 提交说明 | 作者 | 日期 | 工作区 |','| --- | --- | --- | --- | --- | --- | --- |']
    for name,s in sources.items():
        lines.append('| '+' | '.join(cell(v) for v in [name,s['branch'],s['commit'],s['subject'],s['author'],s['date'],'含未提交修改' if s['working_tree_modified'] else '干净'])+' |')
    lines += ['', '> 版本对齐脚本会产生未提交修改；Commit 表示源码基线，实际依赖版本以上表为准。','']
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    md=out/(title+'.md')
    if md.exists(): raise ValueError('Output already exists; use a separate directory for each task')
    md.write_text('\n'.join(lines),encoding='utf-8')
    return (md,)
