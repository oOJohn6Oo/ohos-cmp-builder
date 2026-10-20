#!/usr/bin/env python3
import argparse
import json
import re
from urllib.parse import urlencode
from validate import http, version, log, fail

PUBLIC_MAVEN='https://maven.eazytec-cloud.com/nexus/repository/maven-public/'
SEARCH_API='https://maven.eazytec-cloud.com/nexus/service/rest/v1/search/assets'
FIXED_PREFIXES={
    'kotlin':'2.4.20',
    'skiko':'0.150.1',
    'coroutines_core':'1.11.0',
    'coroutines_cmp':'1.11.0',
    'serialization':'1.11.0',
    'atomicfu':'0.33.0',
    'datetime':'0.8.0',
    'okio':'3.9.1',
}
DEPENDENCIES={
    'kotlin': {'group':'org.jetbrains.kotlin', 'artifact':'kotlin-gradle-plugin',
               'checks':['kotlin-compiler-embeddable','kotlin-stdlib']},
    'skiko': {'group':'org.jetbrains.skiko', 'artifact':'skiko-ohosarm64-api',
              'checks':['skiko-ohosx64-api','skiko-ohosarm64-fusionrenderer','skiko-ohosx64-fusionrenderer']},
    'coroutines_core': {'group':'org.jetbrains.kotlinx', 'artifact':'kotlinx-coroutines-core',
                        'checks':['kotlinx-coroutines-core-ohosarm64','kotlinx-coroutines-core-ohosx64']},
    'coroutines_cmp': {'group':'org.jetbrains.kotlinx', 'artifact':'kotlinx-coroutines-core',
                       'checks':['kotlinx-coroutines-core-ohosarm64','kotlinx-coroutines-core-ohosx64']},
    'serialization': {'group':'org.jetbrains.kotlinx', 'artifact':'kotlinx-serialization-core',
                       'checks':['kotlinx-serialization-core-ohosarm64','kotlinx-serialization-core-ohosx64']},
    'atomicfu': {'group':'org.jetbrains.kotlinx', 'artifact':'atomicfu',
                 'checks':['atomicfu-ohosarm64','atomicfu-ohosx64']},
    'datetime': {'group':'org.jetbrains.kotlinx', 'artifact':'kotlinx-datetime',
                 'checks':['kotlinx-datetime-ohosarm64','kotlinx-datetime-ohosx64']},
    'okio': {'group':'com.squareup.okio', 'artifact':'okio', 'checks':[]},
}


def latest_version(group, artifact, current, dependency):
    version(current)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',group) or '..' in group or group.endswith('.'):
        raise ValueError('Invalid Maven group')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',artifact):
        raise ValueError('Invalid Maven artifact')
    if dependency not in FIXED_PREFIXES: raise ValueError('Unknown dependency')
    info=DEPENDENCIES[dependency]
    if (group,artifact)!=(info['group'],info['artifact']): raise ValueError('Dependency coordinates do not match')
    prefix=FIXED_PREFIXES[dependency]
    coordinates=[artifact]+info['checks']
    required=[name for name in coordinates if 'ohosarm64' in name]
    if not required:
        required=coordinates
        log(dependency+': no OHOS-specific coordinate configured; checking '+', '.join(required))
    query={'repository':'maven-public','maven.groupId':group,'maven.artifactId':artifact,
           'version':prefix+'*','maven.extension':'pom','prerelease':'false',
           'sort':'version','direction':'desc'}
    log(dependency+': querying Nexus API in descending version order; repository=maven-public; fixed prefix='+prefix+'; current='+current)
    seen=set();tokens=set();page_number=0;filtered=0
    while True:
        page=json.loads(http(SEARCH_API+'?'+urlencode(query)))
        if not isinstance(page,dict) or not isinstance(page.get('items'),list): raise ValueError('Invalid Nexus search response')
        page_number+=1
        log(dependency+': API page '+str(page_number)+'; '+str(len(page['items']))+' assets')
        for item in page['items']:
            maven=item.get('maven2',{})
            if (maven.get('groupId'),maven.get('artifactId'),maven.get('extension'))!=(group,artifact,'pom'): continue
            candidate=maven.get('version')
            if not isinstance(candidate,str) or candidate in seen: continue
            seen.add(candidate)
            # Numeric release suffixes exclude SNAPSHOT, RC, beta, dev and compat variants.
            if not re.fullmatch(re.escape(prefix)+r'(?:-\d+(?:[.-]\d+)*)?',candidate):
                filtered+=1
                continue
            version(candidate)
            log(dependency+': checking candidate '+candidate+'; filtered variants='+str(filtered))
            for target in required:
                path=group.replace('.','/')+'/'+target+'/'+candidate+'/'+target+'-'+candidate+'.pom'
                if http(PUBLIC_MAVEN+path,method='HEAD',allow_missing=True) is None:
                    log(dependency+': skip '+candidate+'; missing '+target+' in maven-public; trying next version')
                    break
            else:
                log(dependency+': selected='+candidate+'; verified '+', '.join(required))
                return candidate
        token=page.get('continuationToken')
        if not token: break
        if not isinstance(token,str) or token in tokens: raise ValueError('Invalid Nexus pagination token')
        tokens.add(token);query['continuationToken']=token
    raise ValueError('No public release for '+artifact+' with prefix '+prefix+' and required artifacts: '+', '.join(required))


def main():
    parser=argparse.ArgumentParser(description='Find the latest public Maven suffix with a fixed version prefix.')
    parser.add_argument('--group',required=True)
    parser.add_argument('--artifact',required=True)
    parser.add_argument('--current',required=True)
    parser.add_argument('--dependency',choices=FIXED_PREFIXES,required=True)
    args=parser.parse_args()
    print(latest_version(args.group,args.artifact,args.current,args.dependency))


if __name__=='__main__':
    try: main()
    except Exception as error: fail(error)
