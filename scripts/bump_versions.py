#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from validate import DEPENDENCIES, log, version, fail

def align_versions(core, cmp, suffix, overrides=None, dry_run=False):
    overrides=overrides or {}
    log('Version alignment started'+(' (dry run)' if dry_run else ''))
    suffix=suffix.strip().lstrip('-')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',suffix) or not suffix[-1].isdigit() or suffix.startswith(('1.12.0','1.9.2')): raise ValueError('Invalid version suffix')
    for value in overrides.values(): version(value)
    paths={'core_properties':core/'gradle.properties','core_catalog':core/'gradle/libs.versions.toml',
           'cmp_properties':cmp/'components/gradle.properties','plugin_properties':cmp/'gradle-plugins/gradle.properties',
           'cmp_catalog':cmp/'components/gradle/libs.versions.toml','plugin_catalog':cmp/'gradle-plugins/gradle/libs.versions.toml',
           'har':core/'compose/ui/ui-arkui/src/ohosArm64Main/cpp/compose/oh-package.json5'}
    for path in paths.values():
        if path.is_symlink(): raise ValueError('Version file must not be a symlink')
    originals={key:path.read_bytes().decode() for key,path in paths.items()}; texts=dict(originals)
    def locate(key, field):
        text=texts[key]; start,end=0,len(text)
        if key.endswith('catalog'):
            section=re.search(r'^\[versions\][ \t]*\r?\n(.*?)(?=^\[|\Z)',text,re.M|re.S)
            if not section: raise ValueError('Missing versions section')
            start,end=section.span(1)
            pattern=r'^'+re.escape(field)+r'[ \t]*=[ \t]*"([^"\r\n]+)"[ \t]*(?:#[^\r\n]*)?\r?$'
        else: pattern=r'^'+re.escape(field)+r'[ \t]*=[ \t]*([^\r\n]+)\r?$'
        matches=list(re.finditer(pattern,text[start:end],re.M))
        if len(matches)!=1: raise ValueError('Expected one '+field+' in '+key)
        match=matches[0]; value=match.group(1).rstrip(); version(value)
        return start+match.start(1),start+match.start(1)+len(value),value
    def read(key,field): return locate(key,field)[2]
    def put(key,field,value):
        start,end,old=locate(key,field)
        if old!=value:
            texts[key]=texts[key][:start]+value+texts[key][end:]
            log(key+': '+field+' '+old+' -> '+value)
    def shared(name,entries):
        values=[read(*entry) for entry in entries]; chosen=overrides.get(name,values[0])
        if name not in overrides and len(set(values))!=1: raise ValueError(name+' defaults differ; select an explicit version')
        for entry in entries: put(*entry,chosen)
        return chosen
    for key,field in [('core_properties','jetbrains.publication.version.COMPOSE'),('cmp_properties','compose.version'),('plugin_properties','compose.version')]:
        if not re.match(r'^1\.12\.0(?:-|$)',read(key,field)): raise ValueError('Only 1.12.0 source branches supported')
    kotlin=shared('kotlin',[('core_catalog','kotlin'),('cmp_properties','kotlin.version'),('plugin_catalog','kotlin'),('plugin_properties','compose.tests.kotlin.version')])
    for field in ('composeCompilerPlugin','kotlinGradlePluginAnnotations','kotlinGradlePluginApi','kotlinNativeUtils','kotlinToolingCore'): put('core_catalog',field,kotlin)
    shared('skiko',[('core_catalog','skikoOhos'),('cmp_catalog','ohosSkiko'),('plugin_properties','compose.ohos.skiko.version')])
    for name,key,field in [('coroutines_core','core_catalog','kotlinCoroutines'),('coroutines_cmp','cmp_catalog','kotlinx-coroutines'),('serialization','core_catalog','kotlinSerialization'),('atomicfu','core_catalog','atomicFu'),('datetime','core_catalog','datetime')]:
        value=read(key,field);put(key,field,overrides.get(name,value))
    if 'okio' in overrides:
        pattern=r'(^okio[ \t]*=[ \t]*\{[^\r\n]*?\bversion[ \t]*=[ \t]*")([^"\r\n]+)(")'
        updated,count=re.subn(pattern,lambda m:m[1]+overrides['okio']+m[3],texts['core_catalog'],flags=re.M)
        if count!=1: raise ValueError('Expected one inline Okio version')
        old_okio=re.search(pattern,texts['core_catalog'],re.M)[2]
        if old_okio!=overrides['okio']: log('core_catalog: okio '+old_okio+' -> '+overrides['okio'])
        texts['core_catalog']=updated
    release='1.12.0-'+suffix
    for key in ('cmp_properties','plugin_properties'):
        for field in ('compose.version','deploy.version'): put(key,field,release)
    put('plugin_properties','compose.material3.version',release)
    for field in ('compose','material3'): put('cmp_catalog',field,release)
    fields=re.findall(r'^(jetbrains\.publication\.version\.[A-Za-z0-9_.-]+)[ \t]*=',texts['core_properties'],re.M)
    if not fields or len(set(fields))!=len(fields): raise ValueError('Missing or duplicate publication versions')
    for field in fields:
        base=re.fullmatch(r'(\d+\.\d+\.\d+)(?:-[A-Za-z0-9._-]+)?',read('core_properties',field))
        if not base: raise ValueError('Unsupported publication version')
        put('core_properties',field,release if field.endswith(('.COMPOSE','.COMPOSE_MATERIAL3')) else base[1]+'-'+suffix)
    put('cmp_catalog','compose-runtime-annotation',read('core_properties','jetbrains.publication.version.RUNTIME_ANNOTATION'))
    manifest=json.loads(texts['har']); previous=manifest['version']
    if previous!=release:
        updated,count=re.subn(r'(^[ \t]*"version"[ \t]*:[ \t]*")'+re.escape(previous)+r'(")',lambda m:m[1]+release+m[2],texts['har'],flags=re.M)
        manifest['version']=release
        if count!=1 or json.loads(updated)!=manifest: raise ValueError('Ambiguous HAR manifest version')
        texts['har']=updated
        log('HAR package: '+previous+' -> '+release)
    if not dry_run:
        staged=[]
        try:
            for key,path in paths.items():
                if texts[key]==originals[key]: continue
                if path.read_bytes().decode()!=originals[key]: raise ValueError('Source changed during preparation')
                fd,name=tempfile.mkstemp(prefix='.cmp-version-',dir=path.parent);staged.append((name,path))
                with os.fdopen(fd,'wb') as out: out.write(texts[key].encode())
                os.chmod(name,path.stat().st_mode & 0o7777)
            for name,path in staged: os.replace(name,path)
        finally:
            for name,_ in staged:
                if os.path.exists(name): os.unlink(name)

    changed=sum(texts[key]!=originals[key] for key in paths)
    log('Version alignment '+('preview' if dry_run else 'complete')+': '+release+'; changed files='+str(changed))


def main():
    parser=argparse.ArgumentParser(description='Align CMP/Core dependencies and publication versions.')
    parser.add_argument('--core',type=Path,required=True)
    parser.add_argument('--cmp',type=Path,required=True)
    parser.add_argument('--suffix',required=True)
    for name in DEPENDENCIES:
        parser.add_argument('--'+name.replace('_','-'))
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    overrides={name:getattr(args,name) for name in DEPENDENCIES if getattr(args,name) is not None}
    align_versions(args.core,args.cmp,args.suffix,overrides,args.dry_run)
    log('Dry run: no files changed' if args.dry_run else 'Versions updated')


if __name__=='__main__':
    try: main()
    except Exception as error: fail(error)
