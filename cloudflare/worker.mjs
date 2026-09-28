// Native Fetch / Web Crypto / D1 only. No upstream Codex credentials or logs.
const json = (value, status = 200) => Response.json(value, {status, headers:{'Cache-Control':'private, no-store','Retry-After':'60'}});
const digest = async value => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),x=>x.toString(16).padStart(2,'0')).join('');
const id = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{16,64}$/.test(value);
const secret = value => typeof value === 'string' && /^[a-zA-Z0-9_-]{43}$/.test(value);
const datasets = ['live','recent','trends'];
const fields = new Set(['name','timeZone','observed','task','runningCount','today','five','week','remaining','reset','retained','tokens','cost','requests','costComplete','hitRate','id','started','status','preview','model','effort','speed','duration','daily','periods','start','metric','days','total','models']);
function validPayload(value, depth = 0) {
  if (depth > 8) return false;
  if (value === null || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER;
  if (typeof value === 'string') return new TextEncoder().encode(value).length <= 240;
  if (Array.isArray(value)) return value.length <= 200 && value.every(x=>validPayload(x,depth+1));
  return typeof value === 'object' && Object.entries(value).every(([key,item])=>fields.has(key)&&validPayload(item,depth+1));
}
async function body(request, max = 262144) {
  if (!request.headers.get('content-type')?.startsWith('application/json')) throw new Error('body');
  const reader = request.body?.getReader(); if (!reader) throw new Error('body');
  let length = 0; const chunks=[];
  while(true) {const part=await reader.read(); if(part.done) break; length+=part.value.length; if(length>max){await reader.cancel();throw new Error('body');} chunks.push(part.value);}
  const data=new Uint8Array(length);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.length;}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(data));
}
export default {
  async fetch(request, env) {
    try {
      const url=new URL(request.url), parts=url.pathname.split('/').filter(Boolean);
      if(url.pathname==='/health' && request.method==='GET') return json({service:'codexio-sync',protocol:1});
      if(parts[0]!=='v1') return json({error:'NOT_FOUND'},404);
      const bearer=request.headers.get('authorization')?.replace(/^Bearer /,'') ?? '';
      if(bearer.length<43 || bearer.length>160) return json({error:'UNAUTHORIZED'},401);
      if(env.RATE && !(await env.RATE.limit({key:request.headers.get('cf-connecting-ip')??'unknown'})).success) return json({error:'RATE_LIMIT'},429);
      const db=env.DB, tokenHash=await digest(bearer), now=Math.floor(Date.now()/1000);
      // Account-sharing guard: at most 20k admitted requests/day. Normal admitted
      // operations write <= 4 rows; indexes only cover immutable primary keys.
      const day=new Date().toISOString().slice(0,10);
      const budget=await db.prepare('INSERT INTO budget(day,n) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET n=n+1 WHERE n < ? RETURNING n').bind(day,Number(env.DAILY_REQUEST_BUDGET??20000)).first();
      if(!budget) return json({error:'DAILY_BUDGET'},429);
      if(parts[1]==='enroll' && parts.length===2 && request.method==='POST') {
        const b=await body(request,2048);
        if(!id(b.host)||!secret(b.writer)||typeof b.name!=='string'||b.name.length>40) return json({error:'INVALID'},400);
        const writerHash=await digest(b.writer);
        const result=await db.batch([
          db.prepare('UPDATE invites SET host=? WHERE hash=? AND expires>? AND (host IS NULL OR host=?)').bind(b.host,tokenHash,now,b.host),
          db.prepare('INSERT INTO hosts(id,writer_hash,name) SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM invites WHERE hash=? AND host=? AND expires>?) ON CONFLICT(id) DO NOTHING').bind(b.host,writerHash,b.name,tokenHash,b.host,now),
          db.prepare('SELECT id FROM hosts WHERE id=? AND writer_hash=? AND EXISTS(SELECT 1 FROM invites WHERE hash=? AND host=? AND expires>?)').bind(b.host,writerHash,tokenHash,b.host,now)
        ]);
        return result[2].results.length ? json({ok:true}) : json({error:'INVITE_INVALID'},403);
      }
      const host=parts[2];if(parts[1]!=='hosts'||!id(host))return json({error:'INVALID'},400);
      const writer=await db.prepare('SELECT id FROM hosts WHERE id=? AND writer_hash=?').bind(host,tokenHash).first();
      if(parts[3]==='readers' && writer && request.method==='PUT' && parts.length===4) {
        const b=await body(request,4096);if(!id(b.id)||!secret(b.secret))return json({error:'INVALID'},400);
        const row=await db.prepare('INSERT INTO readers(host,id,token_hash) SELECT ?,?,? WHERE (SELECT count(*) FROM readers WHERE host=?) < 3 OR EXISTS(SELECT 1 FROM readers WHERE host=? AND id=?) ON CONFLICT(host,id) DO UPDATE SET token_hash=excluded.token_hash RETURNING id').bind(host,b.id,await digest(b.secret),host,host,b.id).first();
        return row?json({ok:true}):json({error:'READER_LIMIT'},409);
      }
      if(parts[3]==='readers' && writer && request.method==='DELETE' && parts.length===5 && id(parts[4])) {
        await db.prepare('DELETE FROM readers WHERE host=? AND id=?').bind(host,parts[4]).run();return json({ok:true});
      }
      if(parts[3]==='data' && parts.length===5 && datasets.includes(parts[4]) && writer && request.method==='PUT') {
        const b=await body(request), dataset=parts[4];
        if(b.dataset!==dataset||!Number.isSafeInteger(b.revision)||b.revision<1||typeof b.payload!=='string'||new TextEncoder().encode(b.payload).length>({live:8000,recent:120000,trends:80000}[dataset])||await digest(b.payload)!==b.digest)return json({error:'INVALID'},400);
        const p=JSON.parse(b.payload);if(!validPayload(p)||dataset==='recent'&&!Array.isArray(p)||dataset==='trends'&&(!Array.isArray(p.daily)||p.daily.length>90||!Array.isArray(p.periods)||p.periods.length>3)||dataset==='live'&&(!p.today||!p.five||!p.week))return json({error:'INVALID'},400);
        const result=await db.prepare('INSERT INTO datasets(host,dataset,revision,digest,payload,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(host,dataset) DO UPDATE SET revision=excluded.revision,digest=excluded.digest,payload=excluded.payload,updated=excluded.updated WHERE excluded.revision>datasets.revision RETURNING revision').bind(host,dataset,b.revision,b.digest,b.payload,now).first();
        if(!result){const old=await db.prepare('SELECT revision,digest FROM datasets WHERE host=? AND dataset=?').bind(host,dataset).first();if(old?.revision!==b.revision||old?.digest!==b.digest)return json({error:'REVISION_CONFLICT'},409);}
        return json({ok:true});
      }
      if(parts[3]==='sync' && parts.length===4 && request.method==='GET') {
        const readerID=url.searchParams.get('reader');if(!id(readerID))return json({error:'UNAUTHORIZED'},401);
        const reader=await db.prepare('SELECT id FROM readers WHERE host=? AND id=? AND token_hash=?').bind(host,readerID,tokenHash).first();
        if(!reader)return json({error:'REVOKED'},403);
        const rows=await db.prepare('SELECT dataset,revision,digest,payload,updated FROM datasets WHERE host=? AND updated>?').bind(host,now-7*86400).all();
        return json({action:'sync',seen:Math.max(0,...rows.results.map(x=>x.updated)),datasets:rows.results.filter(x=>x.revision>Number(url.searchParams.get(x.dataset)??0)).map(({updated,...x})=>x)});
      }
      return json({error:'UNAUTHORIZED'},403);
    } catch { return json({error:'SYNC_UNAVAILABLE'},503); }
  },
  async scheduled(_event,env) {
    const day=new Date(Date.now()-8*86400000).toISOString().slice(0,10);
    await env.DB.batch([env.DB.prepare('DELETE FROM budget WHERE day<?').bind(day),env.DB.prepare('DELETE FROM invites WHERE expires<?').bind(Math.floor(Date.now()/1000)-86400),env.DB.prepare('DELETE FROM datasets WHERE updated<?').bind(Math.floor(Date.now()/1000)-7*86400)]);
  }
};
