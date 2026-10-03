import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'../../tests/browser',fullyParallel:false,use:{baseURL:'http://127.0.0.1:3100'},webServer:{command:'npm run start -- --port 3100',url:'http://127.0.0.1:3100',reuseExistingServer:false},projects:[{name:'desktop',use:{viewport:{width:1280,height:900}}},{name:'mobile',use:{viewport:{width:390,height:844}}}]});
