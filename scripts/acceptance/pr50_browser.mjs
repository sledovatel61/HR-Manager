// Browser tooling stays outside project dependencies. See the acceptance report.
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
const tools=process.env.HR_BROWSER_TOOLS;
if(!tools) throw new Error('Set HR_BROWSER_TOOLS to the external node_modules directory');
const load=path=>import(pathToFileURL(join(tools,path)).href);
const {chromium}=await load('playwright-core/index.mjs');
const {default:binary}=await load('@sparticuz/chromium/build/index.js');
const {inflate}=await load('@sparticuz/chromium/build/lambdafs.js');
const libs=await inflate(join(tools,'@sparticuz/chromium/bin/al2023.tar.br'));
export const browser=await chromium.launch({
 executablePath:await binary.executablePath(),args:['--no-sandbox','--disable-dev-shm-usage'],
 ignoreDefaultArgs:['--hide-scrollbars'],headless:true,
 env:{...process.env,LD_LIBRARY_PATH:join(libs,'lib')},
});
