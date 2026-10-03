import {spawnSync} from 'node:child_process';
for(const args of [['next','build'],['playwright','test']]){const r=spawnSync('npx',['--no-install',...args],{stdio:'inherit',env:{...process.env,NEXT_TELEMETRY_DISABLED:'1'}});if(r.status!==0)process.exit(r.status||1)}

const r=spawnSync('python',['-m','pytest','tests/integration','tests/contract','tests/browser'],{stdio:'inherit',cwd:'../..'});process.exit(r.status??1);
