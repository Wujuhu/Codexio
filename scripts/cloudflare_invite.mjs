// Administrator-only, explicit invitation creation. Credentials stay in memory.
// Usage: node scripts/cloudflare_invite.mjs --create
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {randomBytes,createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
if(process.argv[2]!=='--create') throw new Error('Pass --create to issue a single-use, seven-day invitation.');
const config=JSON.parse(await fs.readFile(path.join(root,'cloudflare/wrangler.jsonc'),'utf8'));
const cli=path.join(root,'build/cache/cloudflare-tools/node_modules/wrangler/bin/wrangler.js');
let token;
try {token=JSON.parse(execFileSync(process.execPath,[cli,'auth','token','--json'],{encoding:'utf8',cwd:path.join(root,'build/cache/cloudflare-tools'),stdio:['ignore','pipe','pipe'],env:{...process.env,WRANGLER_SEND_METRICS:'false',WRANGLER_CACHE_DIR:path.join(root,'build/cache/wrangler')}})).token;}
catch {throw new Error('Cloudflare authorization is unavailable; run Wrangler login.');}
const invite=randomBytes(32).toString('base64url'), hash=createHash('sha256').update(invite).digest('hex'), expires=Math.floor(Date.now()/1000)+7*86400;
const response=await fetch(`https://api.cloudflare.com/client/v4/accounts/${config.account_id}/d1/database/${config.d1_databases[0].database_id}/query`,{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({sql:'INSERT INTO invites(hash,expires) VALUES(?,?)',params:[hash,expires]})});
const result=await response.json();if(!response.ok||!result.success)throw new Error('Invitation creation failed; no token was printed.');
const directory=path.join(root,'build/dev/ios/private');await fs.mkdir(directory,{recursive:true,mode:0o700});
const output=path.join(directory,'invitation-'+Date.now()+'.txt');
await fs.writeFile(output,`Codexio 云端邀请码（单台 Mac 使用；7 天内激活）\n\n${invite}\n\n失效时间：${new Date(expires*1000).toISOString()}\n不要提交 Git 或放进 App。朋友的每台 Mac 需独立创建邀请码。\n`,{encoding:'utf8',mode:0o600,flag:'wx'});
console.log('Invitation saved locally (not printed): '+output);
